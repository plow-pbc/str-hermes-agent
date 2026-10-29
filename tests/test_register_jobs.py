"""What `bin/register-jobs` asks the scheduler for, and when."""
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/hermes-cron-jobs.json"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


register_jobs = _load("register_jobs", ROOT / "bin/register-jobs")

ENV_HOME = {"HOSTEX_TOKEN": "t"}
ENV_HOME_UID = {**ENV_HOME, "PLOW_HOME_CHANNEL": "cht_home"}
ENV_GROUP = {"HOSTEX_TOKEN": "t", "PLOW_CHAT_GROUP_UIDS": "cht_o=STR Owners,cht_c=Cleaners,cht_m=Maintenance",
             "PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}
ENV_SEAM = {**ENV_GROUP, "SEAM_API_KEY": "s"}
OPS_PROP = {"timezone": "America/Los_Angeles",
            "properties": [{"cleaners_thread": "Cleaners", "maintenance_thread": "Maintenance"}]}
BOTH = {"hostex-inbound": "plow_chat", "wiki-nightly": "plow_chat"}

DESIRED = [
    # (id, env, ops, expected {name: deliver})
    # hostex-inbound hands its drafts to the owners' chat itself, so only its
    # failures are delivered, and those are the operator's.
    # turnover wakes each step's thread itself, so it too delivers only failures.
    ("home only", ENV_HOME, {}, BOTH),
    ("owners group", ENV_GROUP, {}, BOTH),
    ("turnover needs seam", ENV_GROUP, OPS_PROP, BOTH),
    ("turnover needs a property", ENV_SEAM, {}, BOTH),
    ("turnover needs the cleaners thread", ENV_SEAM,
     {"properties": [{"cleaners_thread": "Nobody", "maintenance_thread": "Maintenance"}]}, BOTH),
    ("turnover needs the maintenance thread", ENV_SEAM,
     {"properties": [{"cleaners_thread": "Cleaners", "maintenance_thread": "Nobody"}]}, BOTH),
    ("turnover on", ENV_SEAM, OPS_PROP, {**BOTH, "turnover": "plow_chat"}),
]


@pytest.mark.parametrize("env,ops,expected", [d[1:] for d in DESIRED], ids=[d[0] for d in DESIRED])
def test_desired_jobs(env, ops, expected):
    assert {j["name"]: j["deliver"] for j in register_jobs.desired_jobs(env, ops)} == expected


def test_label_missing_refuses():
    env = {"HOSTEX_TOKEN": "t", "PLOW_CHAT_GROUP_UIDS": "cht_c=Cleaners",
           "PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}
    with pytest.raises(SystemExit, match="STR Owners"):
        register_jobs.desired_jobs(env, {})


# Golden: every job is --no-agent and delivers to the operator's home chat (#49),
# a bare plow_chat. hostex-inbound's and turnover's turns reach their threads
# through the plugin's wake inbox (#111); wiki-nightly's keeps guest-derived
# stdout out of a prompt (#44). None is stamped with an origin.
CRON_ARGV = [
    ("hostex-inbound", ENV_GROUP, "every 2m", "hostex-poll.py"),
    ("wiki-nightly", ENV_HOME_UID, "0 3 * * *", "nightly.sh"),
    ("turnover", ENV_SEAM, "every 2m", "turnover-poll.py"),
]


@pytest.mark.parametrize("name,env,schedule,script", CRON_ARGV, ids=[r[0] for r in CRON_ARGV])
def test_create_argv(name, env, schedule, script):
    job = next(j for j in register_jobs.desired_jobs(env, OPS_PROP) if j["name"] == name)
    assert register_jobs.create_argv(job) == [
        register_jobs.HERMES, "cron", "create", schedule, "--name", name,
        "--script", script, "--no-agent", "--deliver", "plow_chat"]


def _registered(**overrides):
    """The fixture's jobs by name, with per-job field overrides."""
    jobs = {j["name"]: j for j in json.loads(FIXTURE.read_text())["jobs"]}
    for name, fields in overrides.items():
        jobs[name.replace("_", "-")] = {**jobs.get(name.replace("_", "-"), jobs["wiki-nightly"]),
                                        "name": name.replace("_", "-"), **fields}
    return jobs


ENV_FIXTURE = {**ENV_GROUP, "PLOW_CHAT_GROUP_UIDS": "cht_owners_fixture=STR Owners"}
CHECKIN = {"script": "checkin-watch.py", "deliver": "plow_chat:cht_owners_fixture", "no_agent": False,
           "schedule": {"kind": "cron", "display": "0 12 * * *"}}

RECONCILE = [
    # (id, env, registered, expected [(kind, name)])
    ("none registered", ENV_FIXTURE, {}, [("create", "hostex-inbound"), ("create", "wiki-nightly")]),
    ("identical", ENV_FIXTURE, _registered(), []),
    ("an agent-drafting hostex-inbound is replaced", ENV_FIXTURE,
     _registered(hostex_inbound={"no_agent": False, "deliver": "plow_chat:cht_owners_fixture"}),
     [("replace", "hostex-inbound")]),
    ("schedule changed", ENV_FIXTURE,
     _registered(wiki_nightly={"schedule": {"kind": "cron", "display": "0 4 * * *"}}),
     [("replace", "wiki-nightly")]),
    ("turnover no longer desired", ENV_FIXTURE, _registered(turnover={"script": "turnover-poll.py"}),
     [("remove", "turnover")]),
    ("a paused retired job is still removed", ENV_FIXTURE,
     _registered(checkin_watch=CHECKIN | {"paused_at": "2026-10-01T00:00:00Z"}), [("remove", "checkin-watch")]),
    ("paused is left alone", ENV_FIXTURE,
     _registered(hostex_inbound={"deliver": "plow_chat", "paused_at": "2026-10-01T00:00:00Z"}), []),
    ("disabled is left alone", ENV_FIXTURE,  # a drifted schedule would otherwise be replaced
     _registered(wiki_nightly={"enabled": False, "schedule": {"kind": "cron", "display": "0 4 * * *"}}), []),
]


@pytest.mark.parametrize("env,registered,expected", [r[1:] for r in RECONCILE],
                         ids=[r[0] for r in RECONCILE])
def test_reconcile(env, registered, expected, capsys):
    actions = register_jobs.reconcile(register_jobs.desired_jobs(env, OPS_PROP), registered)
    assert [(kind, x if kind == "remove" else x["name"]) for kind, x in actions] == expected
    # Never another owner's job: the fixture's deliver-probe is not str's.
    assert "deliver-probe" not in str(actions)
    paused = any(not j["enabled"] or j["paused_at"] for j in registered.values()
                 if j["name"] in register_jobs.OWNED)
    assert ("paused" in capsys.readouterr().out) == paused


def test_checkin_watch_is_removed_and_turnover_registered():
    """An image that ran checkin-watch upgrades to turnover in one reconcile."""
    env = {**ENV_FIXTURE, "SEAM_API_KEY": "s",
           "PLOW_CHAT_GROUP_UIDS": "cht_owners_fixture=STR Owners,cht_c=Cleaners,cht_m=Maintenance"}
    actions = register_jobs.reconcile(register_jobs.desired_jobs(env, OPS_PROP),
                                      _registered(checkin_watch=CHECKIN))
    assert [(kind, x if kind == "remove" else x["name"]) for kind, x in actions] == [
        ("create", "turnover"), ("remove", "checkin-watch")]


def test_registered_jobs_reads_the_live_shape(tmp_path):
    assert set(register_jobs.registered_jobs(FIXTURE)) == {"hostex-inbound", "deliver-probe", "wiki-nightly"}
    assert register_jobs.registered_jobs(tmp_path / "absent.json") == {}
    (tmp_path / "torn.json").write_text("{")
    with pytest.raises(ValueError):
        register_jobs.registered_jobs(tmp_path / "torn.json")


def fail_if_called(argv):
    raise AssertionError(f"runner called: {argv}")


def test_boot_before_setup_skips(capsys):
    assert register_jobs.main(["--boot"], env={}, runner=fail_if_called) == 0
    assert "not set up yet" in capsys.readouterr().out


def test_setup_run_without_a_token_refuses_rather_than_removing_jobs():
    with pytest.raises(SystemExit, match="refusing to remove jobs"):
        register_jobs.main([], env={}, runner=fail_if_called)


def test_an_unreadable_env_file_never_fails_the_boot(tmp_path, monkeypatch, capsys):
    """S6_BEHAVIOUR_IF_STAGE2_FAILS=2 stops the container on a non-zero cont-init."""
    (tmp_path / ".env").write_bytes(b"HOSTEX_TOKEN=\xff\xfe\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert register_jobs.main(["--boot"], runner=fail_if_called) == 0
    err = capsys.readouterr().err
    assert "codec" in err and "the boot carries on" in err


def test_the_file_beats_a_stale_process_env_and_an_empty_group_means_here(tmp_path, monkeypatch):
    """Choosing "here" on a re-run writes PLOW_CHAT_APPROVAL_GROUP empty; a
    stale label inherited from the gateway's start must not override it."""
    env = _home(tmp_path, cursor=True)
    (tmp_path / ".env").write_text("HOSTEX_TOKEN=t\nPLOW_CHAT_APPROVAL_GROUP=\n")
    monkeypatch.setattr(register_jobs.os, "environ", {**env, "PLOW_CHAT_APPROVAL_GROUP": "STR Owners"})
    run = Recorder()
    assert register_jobs.main([], runner=run) == 0
    assert [c[c.index("--deliver") + 1] for c in run.calls] == ["plow_chat", "plow_chat"]


class Recorder:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, argv):
        self.calls.append(argv)
        failed = self.fail is not None and self.fail in argv
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "refused" if failed else "")


def _home(tmp_path, ops="", jobs=None, cursor=False):
    (tmp_path / "repo/vault").mkdir(parents=True)
    (tmp_path / "repo/vault/ops.toml").write_text(ops)
    if jobs is not None:
        (tmp_path / "cron").mkdir()
        (tmp_path / "cron/jobs.json").write_text(json.dumps({"jobs": list(jobs.values())}))
    if cursor:
        (tmp_path / "hostex-poll-cursor.json").write_text("{}")
    return {**ENV_FIXTURE, "HERMES_HOME": str(tmp_path)}


def test_a_cold_cursor_is_primed_before_the_poller_is_created(tmp_path, monkeypatch):
    """The poller's opening tick would otherwise adopt whatever arrived in
    between, and a guest writing in that window is never announced. The cursor
    probed is the one the poller writes."""
    env = _home(tmp_path)
    run = Recorder()
    assert register_jobs.main([], env=env, runner=run) == 0
    assert [c[:3] for c in run.calls] == [
        [str(ROOT / "bin/hostex-poll.py")],
        [register_jobs.HERMES, "cron", "create"],
        [register_jobs.HERMES, "cron", "create"]]
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    poll = _load("hostex_poll", ROOT / "bin/hostex-poll.py")
    assert register_jobs.cursor_path(tmp_path) == poll.cursor_path()


def test_a_warm_cursor_is_not_reprimed_and_a_replace_removes_by_id(tmp_path):
    env = _home(tmp_path, cursor=True, jobs=_registered(hostex_inbound={"no_agent": False}))
    run = Recorder()
    assert register_jobs.main([], env=env, runner=run) == 0
    assert [c[1:4] for c in run.calls] == [["cron", "remove", "job-hostex-inbound"],
                                           ["cron", "create", "every 2m"]]


def test_a_failed_recreate_says_the_job_is_now_missing(tmp_path):
    env = _home(tmp_path, cursor=True, jobs=_registered(hostex_inbound={"no_agent": False}))
    with pytest.raises(SystemExit, match="hostex-inbound was removed and will be recreated on the next run"):
        register_jobs.main([], env=env, runner=Recorder(fail="create"))


@pytest.mark.parametrize("argv,code", [([], SystemExit), (["--boot"], 0)], ids=["setup", "boot"])
def test_a_refused_create_fails_setup_but_never_the_boot(tmp_path, argv, code):
    env = _home(tmp_path, cursor=True)
    run = Recorder(fail="hostex-inbound")
    if code is SystemExit:
        with pytest.raises(SystemExit, match="refused"):
            register_jobs.main(argv, env=env, runner=run)
    else:
        assert register_jobs.main(argv, env=env, runner=run) == code


def test_a_hung_hermes_call_is_a_failure_not_a_hang(monkeypatch):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"], output=b"partial \xff")
    monkeypatch.setattr(register_jobs.subprocess, "run", hang)
    with pytest.raises(SystemExit, match="partial \ufffdtimed out"):
        register_jobs._check(register_jobs._run, [register_jobs.HERMES, "cron", "list"])
