"""cloudbuild.yaml may only use substitutions that Cloud Build (or the trigger) actually provides.

An unknown built-in such as $BUILD_TRIGGER_ID makes the whole build fail to start
("key in the template ... is not a valid built-in substitution"), before any step runs.
"""
import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "cloudbuild.yaml"

# https://cloud.google.com/build/docs/configuring-builds/substitute-variable-values
BUILT_IN = {
    "PROJECT_ID", "PROJECT_NUMBER", "BUILD_ID", "LOCATION", "TRIGGER_NAME", "COMMIT_SHA", "REVISION_ID",
    "SHORT_SHA", "REPO_NAME", "REPO_FULL_NAME", "BRANCH_NAME", "TAG_NAME", "REF_NAME", "TRIGGER_BUILD_CONFIG_PATH",
    "SERVICE_ACCOUNT", "SERVICE_ACCOUNT_EMAIL", "HEAD_BRANCH", "BASE_BRANCH", "HEAD_REPO_URL", "PR_NUMBER",
}
# user substitutions the real trigger defines (gcloud builds triggers list --format="table(name,substitutions)")
TRIGGER_DEFINED = {"_AR_HOSTNAME", "_AR_PROJECT_ID", "_AR_REPOSITORY", "_DEPLOY_REGION", "_PLATFORM", "_SERVICE_NAME", "_TRIGGER_ID"}


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _references(text: str) -> set[str]:
    return set(re.findall(r"(?<!\$)\$\{?([A-Za-z_][A-Za-z0-9_]*)", text))


def test_every_substitution_used_exists():
    cfg = _config()
    defaults = set(cfg.get("substitutions", {}))
    used = _references(CONFIG.read_text(encoding="utf-8"))
    unknown = {name for name in used if name not in BUILT_IN and name not in defaults and name not in TRIGGER_DEFINED}
    assert not unknown, f"substitutions that Cloud Build will reject or leave empty: {sorted(unknown)}"


def test_the_trigger_id_label_uses_the_user_substitution():
    text = CONFIG.read_text(encoding="utf-8")
    assert "BUILD_TRIGGER_ID" not in text
    assert "gcb-trigger-id=$_TRIGGER_ID" in text


def test_deploy_keeps_the_options_the_service_depends_on():
    deploy = next(step for step in _config()["steps"] if step["id"] == "Deploy")["args"]
    for flag in ("--cpu=2", "--memory=4Gi", "--concurrency=3", "--max-instances=10", "--min-instances=1", "--cpu-throttling"):
        assert flag in deploy
    assert "--no-cpu-throttling" not in deploy  # scrapes run in the Cloud Run Job


def test_steps_run_in_the_expected_order():
    steps = _config()["steps"]
    assert [s["id"] for s in steps] == ["Test", "Build", "Deploy", "DeployWorker"]
    assert steps[3]["waitFor"] == ["Deploy"]
