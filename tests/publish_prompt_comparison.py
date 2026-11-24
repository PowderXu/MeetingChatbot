"""Publish the live pytest outcomes to a local Langfuse v4 dataset and verify ingestion."""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree

from langfuse import Langfuse
from langfuse.experiment import Evaluation


def load_outcomes(directory):
    outcomes = {}
    tests = list(ElementTree.parse(directory / "junit.xml").iter("testcase"))
    for recovered in [directory / "recovered.xml", *sorted(directory.glob("recovery-*.xml"))]:
        if not recovered.exists():
            continue
        replacements = {t.attrib["name"]: t for t in ElementTree.parse(recovered).iter("testcase")}
        for index, test in enumerate(tests):
            name = test.attrib["name"]
            if name in replacements:
                if not any(e.tag in {"failure", "error"} and "HTTP 429" in (e.text or "") for e in test):
                    raise ValueError(f"Refusing to replace a completed trial: {name}")
                tests[index] = replacements.pop(name)
        if replacements:
            raise ValueError("Recovery report contains trials outside the original comparison")
    for test in tests:
        trial = test.attrib["name"].partition("[")[2].removesuffix("]")
        if not re.fullmatch(r"(B1|V1|C1|R1|C2|B6|R4)-p[12]-r[12]-round[12]", trial) or trial in outcomes:
            raise ValueError(f"Unexpected or duplicate trial {trial}")
        if test.find("error") is not None:
            raise ValueError(f"Resolve the setup or cleanup error for {trial} before publishing.")
        record = directory / f"{trial}.json"
        data = json.loads(record.read_text()) if record.exists() else {"turns": []}
        failure = test.find("failure")
        if test.find("skipped") is not None:
            verdict = "unexercised"
        elif failure is None:
            verdict = "pass"
        elif not data["turns"] or "Cal.com returned HTTP 429" in (failure.text or ""):
            verdict = "unexercised"
        else:
            verdict = "fail"
        if verdict != "unexercised" and (not data["turns"] or data.get("trial") != trial or not data.get("prompt_sha256")):
            raise ValueError(f"Missing conversation or prompt evidence for {trial}")
        data["verdict"] = verdict
        if failure is not None:
            # Store the failing assertion location; the transcript supplies the evidence.
            data["failed_check"] = next((line.strip() for line in (failure.text or "").splitlines()
                                         if re.search(r"tests/test_round_(one|two)\.py:\d+", line)), "See pytest failure")
        outcomes[trial] = data
    if len(outcomes) != 56:
        raise ValueError(f"Expected 56 planned trials, found {len(outcomes)}")
    return outcomes


def score(*, output, **kwargs):
    if output["verdict"] == "unexercised":
        return [Evaluation(name="exercised", value=False)]
    return [Evaluation(name="exercised", value=True),
            Evaluation(name="calendar_checks_passed", value=output["verdict"] == "pass")]


def dataset_for_comparison(client, cases):
    dataset = "Scheduling prompt comparison"
    client.create_dataset(name=dataset, description="Paired live Cal.com and OpenAI conversations with Mailosaur attendees.")
    for case in cases:
        client.create_dataset_item(dataset_name=dataset, id=str(uuid5(NAMESPACE_URL, dataset + "/" + case)),
                                   input={"case": case}, expected_output={"calendar_checks_passed": True})
    rows = client.get_dataset(dataset)
    if len(rows.items) != len(cases):
        raise ValueError("Local Langfuse dataset does not match this experiment")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    outcomes = load_outcomes(args.directory)
    # Project credentials come from the local instance's headless initialization.
    config = dict(line.split("=", 1) for line in Path(".langfuse-local/.env").read_text().splitlines() if "=" in line)
    client = Langfuse(base_url=config["NEXTAUTH_URL"], public_key=config["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"],
                      secret_key=config["LANGFUSE_INIT_PROJECT_SECRET_KEY"])
    if not client.auth_check():
        raise RuntimeError("Local Langfuse authentication failed")
    cases = sorted({trial.rsplit("-", 1)[0] for trial in outcomes})
    rows = dataset_for_comparison(client, cases)
    dataset = rows.name
    links = {}
    for version in ("round1", "round2"):
        text = (Path(__file__).parent / "prompts" / f"{version}.txt").read_text()
        digest = hashlib.sha256(text.encode()).hexdigest()
        for case in cases:
            observed = outcomes[f"{case}-{version}"].get("prompt_sha256")
            if observed is not None and observed != digest:
                raise ValueError(f"Wrong prompt snapshot for {case}")
        prompt_name = "scheduling-instructions"
        if client.api.prompts.list(name=prompt_name, label=version).data:
            prompt = client.get_prompt(prompt_name, label=version)
            if prompt.prompt != text:
                raise ValueError(f"The registered {version} prompt differs from the snapshot")
        else:
            prompt = client.create_prompt(name=prompt_name, prompt=text, labels=[version], type="text")

        run_name = f"{args.directory.name}-{version}"
        run_id = Langfuse.create_trace_id(seed=dataset + "/" + run_name)
        start = datetime.now(timezone.utc) - timedelta(days=30)

        def recorded_conversation(*, item, **kwargs):
            return outcomes[f"{item.input['case']}-{version}"]

        def read_items():
            return client.api.experiments.list_items(experiment_id=run_id, from_start_time=start,
                                                     fields="core,scores", limit=100).data

        # A publishing retry verifies existing observations instead of creating duplicate scores.
        if not read_items():
            result = rows.run_experiment(name=dataset, run_name=run_name, experiment_id=run_id,
                                         task=recorded_conversation, evaluators=[score], max_concurrency=1,
                                         description="Scored from completed live conversations and independent Cal.com checks.",
                                         metadata={"prompt": prompt.name, "prompt_version": str(prompt.version),
                                                   "prompt_sha256": digest, "model": next(
                                                       o["model"] for o in outcomes.values() if "model" in o)})
            if len(result.item_results) != len(cases) or any(
                    not isinstance(item.output, dict) or "verdict" not in item.output for item in result.item_results):
                raise RuntimeError(f"Langfuse could not score every {version} trial")
            client.flush()
        expected = {row.id: outcomes[f"{row.input['case']}-{version}"]["verdict"] for row in rows.items}
        for _ in range(40):
            items = read_items()
            verified = {}
            for item in items:
                scores = {s.name: s.value for s in (item.scores or [])}
                if scores.get("exercised") == 0:
                    verified[item.experiment_item_id] = "unexercised"
                elif scores.get("exercised") == 1 and "calendar_checks_passed" in scores:
                    verified[item.experiment_item_id] = "pass" if scores["calendar_checks_passed"] else "fail"
            if verified == expected and len(items) == len(expected):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Langfuse has not returned all 28 scored {version} items")
        project = config["LANGFUSE_INIT_PROJECT_ID"]
        links[version] = f"{config['NEXTAUTH_URL']}/project/{project}/experiments/results?baseline={run_id}"
        print(version, links[version], flush=True)
    (args.directory / "langfuse.json").write_text(json.dumps(links, indent=2))
    client.shutdown()


if __name__ == "__main__":
    main()
