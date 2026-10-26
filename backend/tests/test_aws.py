import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import boto3
import cv2
import pytest
import yaml
from moto import mock_aws

from accounts import society_repo
from watchful.components import make_blobs
from watchful.config import Settings
from watchful.metrics import CloudWatchMetrics
from watchful.notify import SnsNotifier
from watchful.store.db import AlertRow, Camera, EventRow, WatchRow
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone

REGION = "us-east-1"


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(Bucket="wf-test")
        yield


def settings(tmp_path, **kw):
    return Settings(session_secret="y", data_dir=tmp_path, aws_region=REGION,
                    s3_bucket="wf-test", _env_file=None, **kw)


@pytest.fixture
def repo(tmp_path, aws):
    return society_repo(settings(tmp_path))


def ev(cam, ts, zone="kitchen", kind="enter"):
    return EventRow(None, cam, ts, kind, 1, "person", zone, {"dwell_s": 3.5}, "k.jpg", [0.1, 0.2], None, "Sana")


def test_repository_contract(repo):
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x", [Zone("z1", "gate", [(0, 0), (1, 0), (1, 1)])],
                                 True, {"falls": {"hold_s": 3}}))
    assert repo.get_camera(cam.id).zones[0].polygon[1] == (1, 0)
    cam.name = "Front gate"
    repo.update_camera(cam)
    assert [c.name for c in repo.list_cameras()] == ["Front gate"]

    repo.add_events([ev(cam.id, 10.5), ev(cam.id, 20, zone="door"), ev(cam.id, 30), ev("other", 15)])
    rows = repo.query_events(camera_id=cam.id, zone="kitchen", since=5, until=25)
    assert [r.ts for r in rows] == [10.5] and rows[0].data == {"dwell_s": 3.5} and rows[0].signature == [0.1, 0.2]
    assert repo.get_event(rows[0].id).person_label == "Sana"
    assert len(repo.query_events(kinds=["enter"])) == 4

    w = repo.add_watch(WatchRow(None, cam.id, "x", {"name": "x"}, True))
    w.enabled = False
    repo.update_watch(w)
    assert repo.list_watches(cam.id)[0].enabled is False

    a = repo.add_alert(AlertRow(None, cam.id, w.id, time.time(), "critical", "alert", "Fell", None, None, [], None, "fp1"))
    assert repo.alert_fingerprint_exists("fp1", 60) and not repo.alert_fingerprint_exists("fp2", 60)
    a.feedback = "up"
    repo.update_alert(a)
    assert repo.get_alert(a.id).feedback == "up" and repo.list_alerts(cam.id)[0].id == a.id

    repo.add_enrollment(Enrollment("p1", "Sana", [0.5] * 4))
    assert [e.name for e in repo.list_enrollments()] == ["Sana"]
    repo.delete_enrollment("p1")
    assert repo.list_enrollments() == []
    repo.record_ai_call()
    repo.record_ai_call()
    assert repo.count_ai_calls_today() == 2
    repo.set_person_label(cam.id, "g1", "Sana")
    assert repo.person_labels(cam.id) == {"g1": "Sana"}
    assert repo.delete_events_before(25) == 3


def test_s3_blobs(tmp_path, aws):
    blobs = make_blobs(settings(tmp_path, storage="s3"))
    blobs.put("cam/kf/1.jpg", b"123", "image/jpeg")
    assert blobs.get("cam/kf/1.jpg") == b"123"
    assert "wf-test" in blobs.url("cam/kf/1.jpg") and "Signature" in blobs.url("cam/kf/1.jpg")


def test_sns_and_cloudwatch(aws):
    topic = boto3.client("sns", region_name=REGION).create_topic(Name="wf")["TopicArn"]
    SnsNotifier(topic, REGION).send("Fall", "Someone fell in the kitchen.")
    m = CloudWatchMetrics(REGION)
    m.put("FramesPerSecond", 5.0, {"Camera": "cam1"})
    m.flush()
    names = [x["MetricName"] for x in boto3.client("cloudwatch", region_name=REGION).list_metrics(
        Namespace="Watchful")["Metrics"]]
    assert "FramesPerSecond" in names


def test_newest_rows_and_index_work(repo):
    repo.add_events([ev("cam9", float(t)) for t in range(10)])
    assert [r.ts for r in repo.query_events(camera_id="cam9", limit=3)] == [7.0, 8.0, 9.0]
    assert [r.ts for r in repo.query_events(limit=2)] == [8.0, 9.0]
    scene = EventRow(None, "cam9", 20.0, "scene", None, None, None, {}, "s.jpg", None, None, None)
    repo.add_events([scene])
    assert [e.id for e in repo.events_missing_embedding(["scene"])] == [scene.id]
    repo.set_event_embedding(scene.id, [0.5, 0.5])
    assert repo.events_missing_embedding(["scene"]) == [] and repo.get_event(scene.id).embedding == [0.5, 0.5]
    for i in range(5):
        repo.add_alert(AlertRow(None, "cam9", None, time.time() + i, "info", "alert", f"a{i}", None, None, [], None,
                                f"f{i}"))
    assert [a.summary for a in repo.list_alerts("cam9", limit=2)] == ["a4", "a3"]
    assert [a.summary for a in repo.list_alerts(limit=1)] == ["a4"]
    assert repo.delete_events_before(5) == 5


# ---- the AWS deployment (deploy/), without AWS: the template applied in moto, deploy.sh against a fake aws ----

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
BRAND = json.loads((ROOT / "brand.json").read_text())
STACK = re.sub(r"[^a-z0-9-]+", "-", BRAND["name"].lower()).strip("-")
MUMBAI = "ap-south-1"
ACCOUNT = "123456789012"
DOMAIN = "society.example.com"
ORIGIN = f"https://{DOMAIN}"
BUCKET = "clips-bucket-test"
NAMESPACE = "Watchful"
MODEL = "anthropic.claude-haiku-5-5"
# metrics the alarms read that the app does not put yet, with what will put them; a strict xfail below fails the
# moment one appears in the code, so its entry is removed and the main check covers it
PENDING_METRICS: dict[str, str] = {}
PENDING_MODULES: dict[str, str] = {}


@pytest.fixture
def mumbai(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_SESSION_TOKEN": "test",
                 "AWS_DEFAULT_REGION": MUMBAI}.items():
        monkeypatch.setenv(k, v)


class CfnLoader(yaml.SafeLoader):
    pass


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {"Ref" if suffix == "Ref" else f"Fn::{suffix}": value}


CfnLoader.add_multi_constructor("!", _intrinsic)
NO_VALUE = object()


def raw_template() -> dict:
    return yaml.load((DEPLOY / "cloudformation.yaml").read_text(), Loader=CfnLoader)


def render(params: dict | None = None) -> dict:
    """The template with parameters, pseudo parameters, Ref, GetAtt, Sub and If resolved; resources get the
    physical names a stack would give them."""
    raw = raw_template()
    values = {k: v.get("Default") for k, v in raw["Parameters"].items()}
    values.update({"Prefix": STACK, "BrandName": BRAND["name"], "Domain": DOMAIN, "CoolAmiId": "ami-0c00100000000000a",
                   "VpcId": "vpc-1234", "AlertEmail": "ops@example.com"}, **(params or {}))
    values.update({"AWS::Region": MUMBAI, "AWS::AccountId": ACCOUNT, "AWS::Partition": "aws"})
    prefix = values["Prefix"]
    refs = {"Bucket": BUCKET, "Instance": "i-0abc", "Ip": "198.51.100.7", "AppLogs": f"/{prefix}/app",
            "AccessLogs": f"/{prefix}/access", "AlertTopic": f"arn:aws:sns:{MUMBAI}:{ACCOUNT}:alerts",
            "Role": "app-role", "Profile": "app-profile"}
    atts = {("Bucket", "Arn"): f"arn:aws:s3:::{BUCKET}", ("SecurityGroup", "GroupId"): "sg-1234",
            ("AppLogs", "Arn"): f"arn:aws:logs:{MUMBAI}:{ACCOUNT}:log-group:/{prefix}/app:*",
            ("AccessLogs", "Arn"): f"arn:aws:logs:{MUMBAI}:{ACCOUNT}:log-group:/{prefix}/access:*"}

    def att(name, attr):
        if attr == "Arn" and name.endswith("Alarm"):
            return f"arn:aws:cloudwatch:{MUMBAI}:{ACCOUNT}:alarm:{name}"
        return atts[(name, attr)]

    def condition(c):
        (fn, arg), = c.items()
        if fn == "Fn::Not":
            return not condition(arg[0])
        if fn == "Fn::Equals":
            return resolve(arg[0]) == resolve(arg[1])
        if fn == "Condition":
            return condition(raw["Conditions"][arg])
        raise ValueError(fn)

    def sub(text, extra):
        def one(m):
            name = m.group(1)
            if name in extra:
                return str(extra[name])
            if "." in name and not name.startswith("AWS::"):
                return att(*name.split(".", 1))
            return str(values[name] if name in values else refs[name])
        return re.sub(r"\$\{([^}!]+)\}", one, text)

    def resolve(node):
        if isinstance(node, list):
            return [v for v in (resolve(x) for x in node) if v is not NO_VALUE]
        if not isinstance(node, dict):
            return node
        if len(node) == 1:
            (key, arg), = node.items()
            if key == "Ref":
                return NO_VALUE if arg == "AWS::NoValue" else values[arg] if arg in values else refs[arg]
            if key == "Fn::GetAtt":
                return att(*arg)
            if key == "Fn::Sub":
                return sub(arg, {}) if isinstance(arg, str) else sub(arg[0], resolve(arg[1]))
            if key == "Fn::If":
                return resolve(arg[1] if condition(raw["Conditions"][arg[0]]) else arg[2])
            if key == "Fn::Base64":
                return resolve(arg)
        return {k: v for k, v in ((k, resolve(v)) for k, v in node.items()) if v is not NO_VALUE}

    return resolve({k: raw[k] for k in ("Resources", "Outputs")})


def props(rendered: dict, name: str) -> dict:
    return rendered["Resources"][name]["Properties"]


def of_type(rendered: dict, kind: str) -> dict:
    return {k: r.get("Properties", {}) for k, r in rendered["Resources"].items() if r["Type"] == kind}


def policy_statements(rendered: dict) -> list[dict]:
    return props(rendered, "Role")["Policies"][0]["PolicyDocument"]["Statement"]


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def make_bucket_from_template(rendered: dict) -> None:
    """Creates the bucket in moto and applies each rule of the template's Bucket."""
    p = props(rendered, "Bucket")
    s3 = boto3.client("s3", region_name=MUMBAI)
    assert p["ObjectLockEnabled"] is True
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": MUMBAI},
                     ObjectLockEnabledForBucket=True)
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration=p["VersioningConfiguration"])
    s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration=p["PublicAccessBlockConfiguration"])
    s3.put_bucket_encryption(Bucket=BUCKET, ServerSideEncryptionConfiguration={"Rules": [
        {"ApplyServerSideEncryptionByDefault": r["ServerSideEncryptionByDefault"]}
        for r in p["BucketEncryption"]["ServerSideEncryptionConfiguration"]]})
    s3.put_bucket_ownership_controls(Bucket=BUCKET, OwnershipControls=p["OwnershipControls"])
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [
        {"ID": r["Id"], "AllowedOrigins": r["AllowedOrigins"], "AllowedMethods": r["AllowedMethods"],
         "AllowedHeaders": r["AllowedHeaders"], "ExposeHeaders": r["ExposedHeaders"], "MaxAgeSeconds": r["MaxAge"]}
        for r in p["CorsConfiguration"]["CorsRules"]]})
    rules = []
    for r in p["LifecycleConfiguration"]["Rules"]:
        rule = {"ID": r["Id"], "Status": r["Status"], "Filter": {"Prefix": r.get("Prefix", "")}}
        if "NoncurrentVersionExpiration" in r:
            rule["NoncurrentVersionExpiration"] = r["NoncurrentVersionExpiration"]
        if "AbortIncompleteMultipartUpload" in r:
            rule["AbortIncompleteMultipartUpload"] = r["AbortIncompleteMultipartUpload"]
        if "ExpirationInDays" in r:
            rule["Expiration"] = {"Days": r["ExpirationInDays"]}
        if r.get("ExpiredObjectDeleteMarker"):
            rule["Expiration"] = {"ExpiredObjectDeleteMarker": True}
        rules.append(rule)
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration={"Rules": rules})


def test_template_bucket_locks_incident_clips_and_leaves_the_database_free(mumbai):
    rendered = render()
    with mock_aws():
        make_bucket_from_template(rendered)
        s3 = boto3.client("s3", region_name=MUMBAI)
        lock = s3.get_object_lock_configuration(Bucket=BUCKET)["ObjectLockConfiguration"]
        assert lock["ObjectLockEnabled"] == "Enabled" and "Rule" not in lock  # no default retention
        assert s3.get_bucket_versioning(Bucket=BUCKET)["Status"] == "Enabled"
        assert all(s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"].values())
        sse = s3.get_bucket_encryption(Bucket=BUCKET)["ServerSideEncryptionConfiguration"]["Rules"][0]
        assert sse["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        cors = s3.get_bucket_cors(Bucket=BUCKET)["CORSRules"]
        assert [(c["AllowedOrigins"], sorted(c["AllowedMethods"])) for c in cors] == [([ORIGIN], ["GET", "HEAD"])]
        rules = {r["ID"]: r for r in s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]}
        assert rules["old-versions"]["NoncurrentVersionExpiration"]["NoncurrentDays"] >= 1
        assert rules["unfinished-uploads"]["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1

        # an incident clip locked until its plan's date: kept, and its version cannot be deleted
        until = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)
        put = s3.put_object(Bucket=BUCKET, Key="ws1/incidents/i1/clip.mp4", Body=b"clip",
                            ObjectLockMode="GOVERNANCE", ObjectLockRetainUntilDate=until)
        head = s3.head_object(Bucket=BUCKET, Key="ws1/incidents/i1/clip.mp4")
        assert (head["ObjectLockMode"], head["ObjectLockRetainUntilDate"]) == ("GOVERNANCE", until)
        with pytest.raises(s3.exceptions.ClientError):
            s3.delete_object(Bucket=BUCKET, Key="ws1/incidents/i1/clip.mp4", VersionId=put["VersionId"])

        # Litestream's writes under db/ carry no lock and can be replaced and removed
        s3.put_object(Bucket=BUCKET, Key="db/watchful/0001.ltx", Body=b"page")
        assert "ObjectLockMode" not in s3.head_object(Bucket=BUCKET, Key="db/watchful/0001.ltx")
        version = s3.list_object_versions(Bucket=BUCKET, Prefix="db/")["Versions"][0]["VersionId"]
        s3.delete_object(Bucket=BUCKET, Key="db/watchful/0001.ltx", VersionId=version)

        s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(props(rendered, "BucketPolicy")["PolicyDocument"]))
        policy = json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"])
        assert policy["Statement"][0]["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_instance_role_is_scoped_to_this_stack(mumbai):
    """Our own policy allows only this stack's bucket, model, metrics namespace, log groups and parameters. The
    managed AmazonSSMManagedInstanceCore (Run Command, Session Manager) still allows other SSM agent actions on *;
    its parameter reads are denied outside /<stack>/ (next test)."""
    rendered = render()
    role = props(rendered, "Role")
    assert role["ManagedPolicyArns"] == ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    statements = policy_statements(rendered)
    allows = [s for s in statements if s["Effect"] == "Allow"]
    for s in allows:
        actions, resources = as_list(s["Action"]), as_list(s["Resource"])
        assert all(a != "*" and not a.endswith(":*") for a in actions), s
        assert "s3:BypassGovernanceRetention" not in actions
        if any(a.startswith("s3:") for a in actions):
            assert set(resources) <= {f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"}
        if any(a.startswith("bedrock:") for a in actions):
            assert actions == ["bedrock:InvokeModel"]  # the Converse API is authorised as InvokeModel
            assert all(r.endswith(f"inference-profile/global.{MODEL}") or r.endswith(f"foundation-model/{MODEL}")
                       for r in resources)
        if "cloudwatch:PutMetricData" in actions:
            assert s["Condition"] == {"StringEquals": {"cloudwatch:namespace": NAMESPACE}}
        if any(a.startswith("logs:") for a in actions):
            assert all(f":log-group:/{STACK}/" in r for r in resources)
        if any(a.startswith("ssm:") for a in actions):
            assert all(r.endswith(f":parameter/{STACK}") or r.endswith(f":parameter/{STACK}/*") for r in resources)
        assert not any(a.startswith(("sns:", "dynamodb:", "iam:", "ec2:")) for a in actions), s
    through_profile = [s for s in allows if "Condition" in s and "bedrock:InferenceProfileArn" in
                       s["Condition"].get("StringEquals", {})]
    assert through_profile and through_profile[0]["Condition"]["StringEquals"]["bedrock:InferenceProfileArn"] \
        .endswith(f":inference-profile/global.{MODEL}")
    granted = {a for s in allows for a in as_list(s["Action"])}
    assert {"s3:PutObjectRetention", "s3:GetObjectRetention", "s3:ListBucketVersions", "s3:DeleteObjectVersion",
            "bedrock:InvokeModel", "cloudwatch:PutMetricData", "logs:PutLogEvents",
            "ssm:GetParametersByPath"} <= granted
    with mock_aws():  # moto checks the policy documents
        iam = boto3.client("iam")
        iam.create_role(RoleName="app", AssumeRolePolicyDocument=json.dumps(role["AssumeRolePolicyDocument"]))
        iam.put_role_policy(RoleName="app", PolicyName="app",
                            PolicyDocument=json.dumps(role["Policies"][0]["PolicyDocument"]))


def locked_writes(rendered: dict) -> list[dict]:
    return [s for s in policy_statements(rendered)
            if {"s3:PutObject", "s3:PutObjectRetention"} & set(as_list(s["Action"]))]


def test_object_lock_writes_are_governance_bounded_and_never_bypassed():
    rendered = render()
    statements = policy_statements(rendered)
    writes = locked_writes(rendered)
    assert [(s["Effect"], sorted(s["Action"])) for s in writes] == [
        ("Allow", ["s3:PutObject", "s3:PutObjectRetention"])]
    condition = writes[0]["Condition"]
    assert condition["StringEqualsIfExists"] == {"s3:object-lock-mode": "GOVERNANCE"}  # never COMPLIANCE
    most = condition["NumericLessThanEqualsIfExists"]["s3:object-lock-remaining-retention-days"]
    limits = raw_template()["Parameters"]["MaxLockDays"]
    assert int(most) == limits["Default"] and 0 < limits["Default"] <= limits["MaxValue"]
    shorter = locked_writes(render({"MaxLockDays": 45}))[0]["Condition"]["NumericLessThanEqualsIfExists"]
    assert int(shorter["s3:object-lock-remaining-retention-days"]) == 45
    bypass = [s for s in statements if "s3:BypassGovernanceRetention" in as_list(s["Action"])]
    assert [(s["Effect"], sorted(s["Resource"])) for s in bypass] == [
        ("Deny", [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"])]


def test_parameter_reads_are_denied_outside_this_stacks_path(mumbai):
    """AmazonSSMManagedInstanceCore allows ssm:GetParameter(s) on every parameter, and SecureStrings under the
    aws/ssm key decrypt for any principal in the account; one Deny keeps the instance to /<stack>/."""
    rendered = render()
    denies = [s for s in policy_statements(rendered) if s["Effect"] == "Deny" and "NotResource" in s]
    assert len(denies) == 1 and "Resource" not in denies[0]
    deny = denies[0]
    assert as_list(deny["Action"]) == ["ssm:GetParameter*"]
    ours = f"arn:aws:ssm:{MUMBAI}:{ACCOUNT}:parameter/{STACK}"
    assert sorted(deny["NotResource"]) == [ours, f"{ours}/*"]
    assert "Condition" not in deny
    # every allowed parameter read lies inside the NotResource, so the Deny never blocks our own settings
    reads = [r for s in policy_statements(rendered) if s["Effect"] == "Allow" and
             any(a.startswith("ssm:GetParameter") for a in as_list(s["Action"])) for r in as_list(s["Resource"])]
    assert reads and set(reads) <= set(deny["NotResource"])
    other = render({"Prefix": "otherapp"})
    assert all("/otherapp" in r for s in policy_statements(other) if "NotResource" in s for r in s["NotResource"])
    with mock_aws():
        iam = boto3.client("iam")
        iam.create_policy(PolicyName="deny", PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [deny]}))


def test_instance_is_graviton_from_the_cool_ami_without_ssh():
    rendered = render()
    instance = props(rendered, "Instance")
    assert instance["InstanceType"] == "c8g.xlarge" and instance["ImageId"] == "ami-0c00100000000000a"
    assert "c8g.large" in raw_template()["Parameters"]["InstanceType"]["AllowedValues"]
    assert instance["BlockDeviceMappings"][0]["DeviceName"] == "/dev/sda1"
    assert "snap install amazon-ssm-agent" in instance["UserData"]
    assert instance["MetadataOptions"]["HttpTokens"] == "required"
    ebs = instance["BlockDeviceMappings"][0]["Ebs"]
    assert ebs["VolumeType"] == "gp3" and ebs["Encrypted"] is True
    assert "SubnetId" not in instance and "KeyName" not in instance
    assert render({"SubnetId": "subnet-9"})["Resources"]["Instance"]["Properties"]["SubnetId"] == "subnet-9"
    group = props(rendered, "SecurityGroup")
    assert sorted((r["FromPort"], r["ToPort"]) for r in group["SecurityGroupIngress"]) == [(80, 80), (443, 443)]
    # out, TCP only: AWS APIs, packages and Telegram on 443, packages on 80, cameras' RTSP on 554
    assert sorted(r["FromPort"] for r in group["SecurityGroupEgress"]) == [80, 443, 554]
    assert {r["IpProtocol"] for r in group["SecurityGroupEgress"]} == {"tcp"}
    assert props(rendered, "Ip")["InstanceId"] == "i-0abc"


AL2023_ARM64 = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"


def test_blank_cool_ami_launches_stock_amazon_linux_from_the_public_ssm_parameter():
    """No COOL subscription: a blank CoolAmiId launches the latest Amazon Linux 2023 arm64 image, resolved by
    CloudFormation from AWS's public parameter, on the same Graviton types; the stack says it is not COOL."""
    raw = raw_template()["Parameters"]
    assert raw["CoolAmiId"]["Default"] == "" and raw["CoolAmiId"]["Type"] == "String"
    pattern = re.compile(raw["CoolAmiId"]["AllowedPattern"])
    assert pattern.fullmatch("") and pattern.fullmatch("ami-0c00100000000000a") and not pattern.fullmatch("x")
    assert raw["BaseAmiParameter"] == {"Type": "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>",
                                       "Default": AL2023_ARM64, "Description": raw["BaseAmiParameter"]["Description"]}
    stock = render({"CoolAmiId": ""})
    instance = props(stock, "Instance")
    assert instance["ImageId"] == AL2023_ARM64 and instance["InstanceType"] == "c8g.xlarge"
    assert instance["BlockDeviceMappings"][0]["DeviceName"] == "/dev/xvda"  # Amazon Linux's root device
    assert "systemctl enable --now amazon-ssm-agent" in instance["UserData"] and "snap" not in instance["UserData"]
    assert instance["MetadataOptions"]["HttpTokens"] == "required" and "KeyName" not in instance
    assert stock["Outputs"]["OpenCvBuild"]["Value"] == "stock OpenCV on Amazon Linux 2023, not COOL"
    assert render()["Outputs"]["OpenCvBuild"]["Value"] == "COOL"
    # everything but the instance's image, disk name and user data is the same stack
    cool = render()
    for name, resource in cool["Resources"].items():
        if name != "Instance":
            assert stock["Resources"][name] == resource, name


def dashboard_metrics(body: str) -> set[str]:
    """Every (namespace, metric) the dashboard reads, from SEARCH expressions and plain metric rows."""
    found = set(re.findall(r'\{(\w+)[^}]*\} MetricName=\\?"(\w+)', body))
    for widget in json.loads(body)["widgets"]:
        for row in widget.get("properties", {}).get("metrics", []):
            if row and isinstance(row[0], str):
                found.add((row[0], row[1]))
    return found


def test_alarms_logs_dashboard_and_budget_apply_in_moto(mumbai):
    rendered = render()
    with mock_aws():
        cw = boto3.client("cloudwatch", region_name=MUMBAI)
        for p in of_type(rendered, "AWS::CloudWatch::Alarm").values():
            cw.put_metric_alarm(**p)
        alarms = {a["AlarmName"]: a for a in cw.describe_alarms()["MetricAlarms"]}
        assert set(alarms) == {f"{STACK}-{n}" for n in ("5xx-rate", "cpu-high", "disk-high", "worker-down",
                                                         "camera-workers", "instance-recover")}
        assert alarms[f"{STACK}-worker-down"]["TreatMissingData"] == "breaching"
        assert alarms[f"{STACK}-camera-workers"]["TreatMissingData"] == "breaching"  # a silent app alarms too
        assert any(":ec2:recover" in a for a in alarms[f"{STACK}-instance-recover"]["AlarmActions"])
        assert alarms[f"{STACK}-disk-high"]["Dimensions"] == [{"Name": "InstanceId", "Value": "i-0abc"}]

        logs = boto3.client("logs", region_name=MUMBAI)
        for p in of_type(rendered, "AWS::Logs::LogGroup").values():
            logs.create_log_group(logGroupName=p["LogGroupName"])
            logs.put_retention_policy(logGroupName=p["LogGroupName"], retentionInDays=p["RetentionInDays"])
        for p in of_type(rendered, "AWS::Logs::MetricFilter").values():
            t = p["MetricTransformations"][0]
            logs.put_metric_filter(logGroupName=p["LogGroupName"], filterName=t["MetricName"],
                                   filterPattern=p["FilterPattern"], metricTransformations=[
                                       {"metricName": t["MetricName"], "metricNamespace": t["MetricNamespace"],
                                        "metricValue": t["MetricValue"]}])
        groups = {g["logGroupName"]: g["retentionInDays"] for g in logs.describe_log_groups()["logGroups"]}
        assert groups == {f"/{STACK}/app": 30, f"/{STACK}/access": 90}

        body = props(rendered, "Dashboard")["DashboardBody"]
        cw.put_dashboard(DashboardName=STACK, DashboardBody=body)
        read = dashboard_metrics(body)
        assert {(NAMESPACE, m) for m in ("FramesPerSecond", "Alerts", "http_5xx", "disk_used_percent",
                                         "mem_used_percent", "worker_up", "camera_workers_down")} <= read
        assert ("AWS/EC2", "CPUUtilization") in read

    budget = props(rendered, "Budget")
    assert budget["Budget"]["BudgetLimit"] == {"Amount": 150, "Unit": "USD"}  # c8g.xlarge all month: about $105
    assert {s["Address"] for n in budget["NotificationsWithSubscribers"] for s in n["Subscribers"]} == {
        "ops@example.com"}
    assert props(rendered, "AlertTopic")["DisplayName"] == f"{BRAND['name']} alerts"


def metric_sources(rendered: dict) -> dict[str, str]:
    """Who puts each metric in our namespace: Caddy's log (metric filters), the CloudWatch agent, or the app."""
    sources = {p["MetricTransformations"][0]["MetricName"]: "access log"
               for p in of_type(rendered, "AWS::Logs::MetricFilter").values()}
    agent = json.loads((DEPLOY / "cloudwatch-agent.json").read_text())["metrics"]
    for plugin, spec in agent["metrics_collected"].items():
        for m in spec["measurement"]:
            sources[f"{plugin}_{m}"] = "cloudwatch agent"
    return sources


def test_every_metric_read_has_a_source_in_our_namespace():
    from watchful.metrics import CloudWatchMetrics
    assert inspect.signature(CloudWatchMetrics).parameters["namespace"].default == NAMESPACE
    agent = json.loads((DEPLOY / "cloudwatch-agent.json").read_text())["metrics"]
    assert agent["namespace"] == NAMESPACE and agent["aggregation_dimensions"] == [["InstanceId"]]
    rendered = render()
    alarmed = {(p["Namespace"], p["MetricName"]) for p in of_type(rendered, "AWS::CloudWatch::Alarm").values()
               if "Namespace" in p}
    alarmed |= {(m["MetricStat"]["Metric"]["Namespace"], m["MetricStat"]["Metric"]["MetricName"])
                for p in of_type(rendered, "AWS::CloudWatch::Alarm").values() for m in p.get("Metrics", [])
                if "MetricStat" in m}
    read = dashboard_metrics(props(rendered, "Dashboard")["DashboardBody"]) | alarmed
    ours = {name for ns, name in read if ns == NAMESPACE}
    sources = metric_sources(rendered)
    source = "\n".join(p.read_text() for p in (ROOT / "backend" / "watchful").rglob("*.py"))
    for name in ours - set(sources) - set(PENDING_METRICS):
        assert f'"{name}"' in source, f"no code puts {name}"
    for namespace, name in read - {(NAMESPACE, n) for n in ours}:
        assert namespace == "AWS/EC2", (namespace, name)


@pytest.mark.parametrize("name", sorted(PENDING_METRICS))
@pytest.mark.xfail(strict=True, reason="not put yet; when this passes, drop the name from PENDING_METRICS")
def test_pending_metric_is_put_by_the_app(name):
    source = "\n".join(p.read_text() for p in (ROOT / "backend" / "watchful").rglob("*.py"))
    assert f'"{name}"' in source, PENDING_METRICS[name]


@pytest.mark.parametrize("module", sorted(PENDING_MODULES))
@pytest.mark.xfail(strict=True, reason="not written yet; when this passes, drop the module from PENDING_MODULES")
def test_pending_module_exists(module):
    assert importlib.util.find_spec(module) is not None, PENDING_MODULES[module]


def test_cfn_lint_is_clean():
    if shutil.which("cfn-lint") is None:
        pytest.skip("cfn-lint is not installed")
    result = subprocess.run(["cfn-lint", str(DEPLOY / "cloudformation.yaml")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ami", ["", "ami-0c00100000000000a", "bogus"])
def test_cfn_lint_is_clean_with_a_blank_cool_ami_and_with_the_cool_ami(tmp_path, ami):
    """cfn-lint with the values deploy.sh passes: a blank CoolAmiId (stock) and a COOL AMI lint clean, and the
    check is real: a value that is no AMI id is flagged."""
    if shutil.which("cfn-lint") is None:
        pytest.skip("cfn-lint is not installed")
    values = {"Prefix": STACK, "BrandName": BRAND["name"], "Domain": DOMAIN, "VpcId": "vpc-12345678",
              "AlertEmail": "ops@example.com", "CoolAmiId": ami}
    (tmp_path / "params.json").write_text(json.dumps([{"ParameterKey": k, "ParameterValue": v}
                                                      for k, v in values.items()]))
    result = subprocess.run(["cfn-lint", str(DEPLOY / "cloudformation.yaml"), "--parameter-files",
                             str(tmp_path / "params.json")], capture_output=True, text=True)
    if ami == "bogus":
        assert result.returncode != 0 and "CoolAmiId" in result.stdout, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


# ---- settings and secrets from SSM ----

def load_deploy_module(name):
    spec = importlib.util.spec_from_file_location(f"deploy_{name}", DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ssm_parameters_join_the_environment_before_the_service_starts(mumbai):
    ssm_env = load_deploy_module("ssm_env")
    token, secret = "tg-" + os.urandom(8).hex(), os.urandom(16).hex()
    with mock_aws():
        ssm = boto3.client("ssm", region_name=MUMBAI)
        for name, value in {f"/{STACK}/TELEGRAM_BOT_TOKEN": token, f"/{STACK}/TELEGRAM_WEBHOOK_SECRET": secret,
                            f"/{STACK}/AI_PROVIDER": "openrouter", f"/{STACK}/lower_case": "x",
                            "/other/STORAGE": "local"}.items():
            ssm.put_parameter(Name=name, Value=value, Type="SecureString")
        environ = {"SSM_PREFIX": f"/{STACK}/", "AWS_REGION": MUMBAI, "AI_PROVIDER": "bedrock", "STORAGE": "s3"}
        ran = []
        ssm_env.main(["--", "python", "-m", "uvicorn", "x"], environ=environ,
                     execvpe=lambda file, args, env: ran.append((file, args, dict(env))))
    (file, args, env), = ran
    assert file == "python" and args == ["python", "-m", "uvicorn", "x"]
    assert env["TELEGRAM_BOT_TOKEN"] == token and env["TELEGRAM_WEBHOOK_SECRET"] == secret
    assert env["AI_PROVIDER"] == "openrouter"  # the owner's parameter wins over the unit's environment file
    assert env["STORAGE"] == "s3" and "lower_case" not in env

    ran.clear()
    ssm_env.main(["--", "true"], environ={}, execvpe=lambda file, args, env: ran.append(args))
    assert ran == [["true"]]  # no SSM_PREFIX: only the command
    assert ssm_env.main(["python"], environ={}, execvpe=lambda *a: ran.append(a)) == 2


# ---- deploy.sh against a fake aws ----

FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
entry = {"args": args}
if "--value" in args and args[args.index("--value") + 1].startswith("file://"):
    entry["file_value"] = open(args[args.index("--value") + 1][7:]).read()
with open(os.environ["FAKE_AWS_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\n")
if os.environ.get("FAKE_AWS_FAIL"):
    sys.exit(99)
op = " ".join(args[:2])
query = args[args.index("--query") + 1] if "--query" in args else ""
stack = os.environ["FAKE_STACK"]
if op == "sts get-caller-identity":
    print("111122223333")
elif op == "ec2 describe-vpcs":
    print("vpc-1234")
elif op == "cloudformation describe-stacks":
    print(f"InstanceId\ti-0abc\nPublicIp\t198.51.100.7\nBucketName\t{stack}-bucket-x\nAppLogGroup\t/{stack}/app\n"
          f"AccessLogGroup\t/{stack}/access\nBedrockModel\tglobal.anthropic.claude-haiku-5-5\n"
          "DashboardUrl\thttps://c/d")
elif op == "ssm put-parameter":
    if "--overwrite" not in args and args[args.index("--name") + 1].endswith("/TELEGRAM_WEBHOOK_SECRET"):
        print("An error occurred (ParameterAlreadyExists) when calling the PutParameter operation", file=sys.stderr)
        sys.exit(254)
    print("1")
elif op == "s3 presign":
    print(f"https://{stack}-bucket-x.s3.ap-south-1.amazonaws.com/releases/r1.tar.gz?X-Amz-Signature=abc")
elif op == "ssm describe-instance-information":
    print("Online")
elif op == "ssm send-command":
    print("cmd-1")
elif op == "ssm get-command-invocation":
    print({"Status": "Success", "StandardOutputContent": "healthz ok from the instance"}.get(query, ""))
'''

SECRETS = {"TELEGRAM_BOT_TOKEN": "123456:tg-" + os.urandom(8).hex(),
           "GOOGLE_CLIENT_SECRET": "gcs-" + os.urandom(8).hex(), "OPENROUTER_API_KEY": "sk-or-" + os.urandom(8).hex()}


@pytest.fixture
def fake_aws(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(FAKE_AWS)
    (bin_dir / "aws").chmod(0o755)
    params = (DEPLOY / "params.env.example").read_text()
    values = {"AWS_PROFILE": f"{STACK}-test", "COOL_AMI_ID": "ami-0fakecool", "ALERT_EMAIL": "ops@example.com",
              "POLAR_PRODUCTS": "'{\"society_month\": \"p1\"}'", **SECRETS}
    for name, value in values.items():
        params, count = re.subn(rf"^{name}=.*$", f"{name}={value}", params, flags=re.M)
        assert count == 1, name
    (tmp_path / "params.env").write_text(params)
    tarball = tmp_path / "r1.tar.gz"
    tarball.write_bytes(b"release")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_AWS_LOG": str(tmp_path / "aws.log"),
           "FAKE_STACK": STACK, "RELEASE_TARBALL": str(tarball)}

    def run(*args, **extra_env):
        result = subprocess.run(["bash", str(DEPLOY / "deploy.sh"), "--params", str(tmp_path / "params.env"), *args],
                                capture_output=True, text=True, env={**env, **extra_env}, timeout=120)
        log = tmp_path / "aws.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return run


def test_deploy_dry_run_prints_every_aws_command_and_runs_none(fake_aws):
    result, calls = fake_aws("--dry-run", FAKE_AWS_FAIL="1")
    assert result.returncode == 0, result.stderr
    assert calls == []
    out = result.stdout
    for wanted in (f"+ aws cloudformation deploy --stack-name {STACK}", f"Prefix={STACK}", f"BrandName={BRAND['name']}",
                   f"Domain={BRAND['domain']}", "CoolAmiId=ami-0fakecool", "InstanceType=c8g.xlarge",
                   "MonthlyBudgetUsd=150", "CAPABILITY_IAM",
                   f"+ aws ssm put-parameter --name /{STACK}/TELEGRAM_BOT_TOKEN --type SecureString",
                   "--value file://\\<TELEGRAM_BOT_TOKEN\\>", f"/{STACK}/TELEGRAM_WEBHOOK_SECRET",
                   f"/{STACK}/SESSION_SECRET", "+ aws s3 cp", "+ aws s3 presign", "+ aws ssm send-command",
                   "remote_setup.sh", f"--profile {STACK}-test --region {MUMBAI}"):
        assert wanted in out, wanted
    assert f"/{STACK}/DOMAIN" not in out and f"/{STACK}/GOOGLE_CLIENT_ID" not in out  # deploy names, blank names
    for secret in SECRETS.values():
        assert secret not in out + result.stderr


def test_deploy_runs_in_order_and_secrets_never_reach_the_command_line(fake_aws, tmp_path):
    result, calls = fake_aws(AWS_RESULTS=str(tmp_path / "results" / "aws.json"))
    assert result.returncode == 0, result.stdout + result.stderr
    written = json.loads((tmp_path / "results" / "aws.json").read_text())
    assert {k: written[k] for k in ("region", "stack", "instance_type", "code", "url")} == {
        "region": MUMBAI, "stack": STACK, "instance_type": "c8g.xlarge", "code": "r1",
        "url": f"https://{BRAND['domain']}"}
    assert written["outputs"]["InstanceId"] == "i-0abc" and "memory_mb" not in written  # no check line: left out
    ops = [" ".join(c["args"][:2]) for c in calls]
    first = {op: ops.index(op) for op in reversed(ops)}
    assert first["sts get-caller-identity"] < first["cloudformation deploy"] < first["ssm put-parameter"] \
        < first["s3 cp"] < first["ssm send-command"] < first["ssm get-command-invocation"]
    assert all(c["args"][-4:] == ["--profile", f"{STACK}-test", "--region", MUMBAI] for c in calls)

    puts = {c["args"][c["args"].index("--name") + 1]: c for c in calls if c["args"][:2] == ["ssm", "put-parameter"]}
    for name, secret in SECRETS.items():
        assert puts[f"/{STACK}/{name}"]["file_value"] == secret and "--overwrite" in puts[f"/{STACK}/{name}"]["args"]
    assert puts[f"/{STACK}/POLAR_PRODUCTS"]["file_value"] == '{"society_month": "p1"}'
    assert re.fullmatch(r"[0-9a-f]{64}", puts[f"/{STACK}/SESSION_SECRET"]["file_value"])
    assert "--overwrite" not in puts[f"/{STACK}/SESSION_SECRET"]["args"]  # generated once, never replaced
    assert "--overwrite" not in puts[f"/{STACK}/DATA_KEY"]["args"]  # camera addresses stay readable
    assert puts[f"/{STACK}/DATA_KEY"]["file_value"] != puts[f"/{STACK}/SESSION_SECRET"]["file_value"]
    assert re.fullmatch(r"[0-9a-f]{64}", puts[f"/{STACK}/TELEGRAM_WEBHOOK_SECRET"]["file_value"])
    assert "TELEGRAM_WEBHOOK_SECRET kept" in result.stdout
    assert not {f"/{STACK}/DOMAIN", f"/{STACK}/GOOGLE_CLIENT_ID", f"/{STACK}/AWS_PROFILE"} & set(puts)
    every_arg = " ".join(a for c in calls for a in c["args"])
    for secret in SECRETS.values():
        assert secret not in every_arg and secret not in result.stdout + result.stderr

    send = next(c["args"] for c in calls if c["args"][:2] == ["ssm", "send-command"])
    commands = json.loads(send[send.index("--parameters") + 1])["commands"]
    script = "\n".join(commands)
    assert f"curl -fsSL --retry 3 -o /opt/watchful/releases/r1.tar.gz 'https://{STACK}-bucket-x" in script
    assert f"BUCKET='{STACK}-bucket-x'" in script and f"APP_LOG_GROUP='/{STACK}/app'" in script
    assert commands[-1].endswith("bash /opt/watchful/releases/r1/deploy/remote_setup.sh")
    assert "healthz ok from the instance" in result.stdout


def stock_params(tmp_path) -> None:
    params = tmp_path / "params.env"
    text, count = re.subn(r"^COOL_AMI_ID=.*$", "COOL_AMI_ID=", params.read_text(), flags=re.M)
    assert count == 1
    params.write_text(text)


def test_deploy_dry_run_shows_the_stock_path_when_cool_ami_is_blank(fake_aws, tmp_path):
    stock_params(tmp_path)
    result, calls = fake_aws("--dry-run", FAKE_AWS_FAIL="1")
    assert result.returncode == 0, result.stderr
    assert calls == []
    out = result.stdout
    assert "stock OpenCV, not COOL" in out and "al2023-ami-kernel-default-arm64" in out
    assert re.search(r"--parameter-overrides .* CoolAmiId= ", out) and "(build stock)" in out
    assert "COOL: the instance launches" not in out


def test_deploy_in_stock_mode_records_no_cool(fake_aws, tmp_path):
    stock_params(tmp_path)
    result, calls = fake_aws(AWS_RESULTS=str(tmp_path / "results" / "aws.json"))
    assert result.returncode == 0, result.stdout + result.stderr
    deploy = next(c["args"] for c in calls if c["args"][:2] == ["cloudformation", "deploy"])
    assert "CoolAmiId=" in deploy
    written = json.loads((tmp_path / "results" / "aws.json").read_text())
    assert written["cool"] is False and written["build"] == "stock"
    assert written["opencv_build"] == "stock OpenCV, not COOL"


def test_aws_results_reads_what_the_instance_checked():
    setup = "\n".join([
        'healthz {"ok": true, "opencv": "5.0.0", "kleidicv": true}',
        "cv2 5.0.0 from /opt/cool/venvs/python_3.12/lib/python3.12/site-packages/cv2/__init__.py",
        "KleidiCV loaded", "COOL yes: cv2 is loaded from under /opt/cool",
        "video h264.mp4 (H.264 MP4): 30 of 30 frames decoded, ok",
        "video hevc.mp4 (H.265 MP4): 0 of 30 frames decoded, FAIL",
        "memory: 2630 MB in 7 processes, budget 6144 MB: ok"])
    data = load_deploy_module("aws_results").results(MUMBAI, STACK, "c8g.xlarge", DOMAIN, "abc123",
                                                    "InstanceId\ti-1\nPublicIp\t198.51.100.7", setup)
    assert data["healthz"] == {"ok": True, "opencv": "5.0.0", "kleidicv": True}
    assert data["cv2_file"].startswith("/opt/cool/") and data["opencv"] == "5.0.0"
    assert data["kleidicv"] is True and data["cool"] is True
    assert data["video"] == {"h264.mp4": True, "hevc.mp4": False}
    assert (data["memory_mb"], data["memory_processes"], data["memory_ok"]) == (2630, 7, True)
    assert data["outputs"] == {"InstanceId": "i-1", "PublicIp": "198.51.100.7"}
    assert data["url"] == f"https://{DOMAIN}" and "upload_check" not in data
    assert "opencv_build" not in data and "build" not in data  # no build line, no mode: nothing claimed


def test_aws_results_on_stock_opencv_never_claim_cool():
    setup = "\n".join([
        "cv2 5.0.0 from /opt/watchful/venv/lib/python3.12/site-packages/cv2/__init__.py",
        "KleidiCV loaded", "COOL no: cv2 is not loaded from under /opt/cool",
        "opencv build: stock OpenCV, not COOL", "machine: aarch64",
        "video h264.mp4 (H.264 MP4): 30 of 30 frames decoded, ok",
        "video hevc.mp4 (H.265 MP4): 30 of 30 frames decoded, ok"])
    results = load_deploy_module("aws_results").results
    data = results(MUMBAI, STACK, "c8g.xlarge", DOMAIN, "abc123", "", setup, build="stock")
    assert data["cool"] is False and data["build"] == "stock" and data["machine"] == "aarch64"
    assert data["opencv"] == "5.0.0" and data["opencv_build"] == "stock OpenCV, not COOL"
    assert data["video"] == {"h264.mp4": True, "hevc.mp4": True}
    # even a setup output that claims COOL is overruled by the deploy's own mode
    claims = results(MUMBAI, STACK, "c8g.xlarge", DOMAIN, "abc123", "", "COOL yes: cv2 is", build="stock")
    assert claims["cool"] is False and claims["opencv_build"] == "stock OpenCV, not COOL"


# ---- checks run on the instance ----

def test_cv2_info_requires_cool_by_the_file_it_loaded(tmp_path, monkeypatch, capsys):
    info = load_deploy_module("cv2_info")

    class Fake:
        __version__ = "5.0.0"

        def __init__(self, file, build):
            self.__file__, self.build = file, build

        def getBuildInformation(self):
            return self.build

        def __getattr__(self, name):  # video I/O is this machine's cv2
            return getattr(cv2, name)

    monkeypatch.setenv("COOL_ROOT", str(tmp_path / "cool"))
    cool = Fake(str(tmp_path / "cool" / "cv2" / "__init__.py"), "  HAL: KleidiCV (ver 0.7.0)")
    stock_arm = Fake(str(tmp_path / "venv" / "cv2" / "__init__.py"), "  HAL: KleidiCV (ver 0.3.0)")
    no_hal = Fake(str(tmp_path / "cool" / "cv2" / "__init__.py"), "  HAL: NO")
    assert info.main(["--require-cool"], cool) == 0
    assert info.main(["--require-cool"], stock_arm) == 4  # KleidiCV alone is not COOL
    assert info.main(["--require-kleidicv"], stock_arm) == 0
    assert info.main(["--require-cool"], no_hal) == 3
    assert info.main([], no_hal) == 0
    assert "COOL no: cv2 is not loaded from under" in capsys.readouterr().out

    class NoFfmpeg(Fake):  # a build without FFmpeg video I/O: no camera stream or recording would open
        videoio_registry = SimpleNamespace(
            getBackends=lambda: [b for b in cv2.videoio_registry.getBackends() if b != cv2.CAP_FFMPEG],
            getBackendName=cv2.videoio_registry.getBackendName)

    assert info.main(["--require-cool"], NoFfmpeg(cool.__file__, cool.build)) == 5
    assert info.main([], NoFfmpeg(cool.__file__, cool.build)) == 0  # printing alone never fails
    assert "FFMPEG is not among" in capsys.readouterr().out


def test_cv2_info_decodes_camera_video_on_stock_opencv(capsys):
    """Cameras and their recorders send H.264 and H.265: the deploy fails unless the app's cv2 decodes a two second
    clip of each to the last frame."""
    info = load_deploy_module("cv2_info")
    assert info.main(["--require-video"]) == 0
    out = capsys.readouterr().out
    assert "FFMPEG" in out and "h264.mp4" in out and "hevc.mp4" in out and "FAIL" not in out
    for name, codec in (("h264.mp4", "h264"), ("hevc.mp4", "hevc")):
        clip = DEPLOY / "clips" / name
        assert clip.stat().st_size < 50_000  # tiny, committed
        cap = cv2.VideoCapture(str(clip))
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode().lower()
        assert fourcc in {"h264": ("avc1", "h264"), "hevc": ("hvc1", "hev1", "hevc")}[codec], (name, fourcc)
        frames = 0
        while cap.grab():
            frames += 1
        assert frames == info.CLIP_FRAMES, name

    class Undecodable:  # FFMPEG listed, but the clips do not open
        VideoCapture = staticmethod(lambda *a: cv2.VideoCapture())

        def __getattr__(self, name):
            return getattr(cv2, name)

    assert info.main(["--require-video"], Undecodable()) == 5
    assert re.search(r'cv2_info\.py" --require-cool', (DEPLOY / "remote_setup.sh").read_text())


def test_cv2_info_stock_mode_requires_opencv_5_on_arm64_and_decoding(tmp_path, monkeypatch, capsys):
    info = load_deploy_module("cv2_info")

    class Stock:
        def __init__(self, version="5.0.0"):
            self.__version__, self.__file__ = version, str(tmp_path / "venv" / "cv2" / "__init__.py")

        def getBuildInformation(self):
            return "  HAL: KleidiCV (ver 0.3.0)"

        def __getattr__(self, name):
            return getattr(cv2, name)

    monkeypatch.setenv("COOL_ROOT", str(tmp_path / "cool"))
    monkeypatch.setattr(info.platform, "machine", lambda: "aarch64")
    assert info.main(["--require-stock"], Stock()) == 0
    out = capsys.readouterr().out
    assert "opencv build: stock OpenCV, not COOL" in out and "machine: aarch64" in out and "COOL no" in out
    assert info.main(["--require-stock"], Stock("4.12.0")) == 6
    assert info.main(["--require-cool"], Stock()) == 4  # the stock check is never the COOL proof

    class NoFfmpeg(Stock):
        videoio_registry = SimpleNamespace(
            getBackends=lambda: [b for b in cv2.videoio_registry.getBackends() if b != cv2.CAP_FFMPEG],
            getBackendName=cv2.videoio_registry.getBackendName)

    assert info.main(["--require-stock"], NoFfmpeg()) == 5
    monkeypatch.setattr(info.platform, "machine", lambda: "x86_64")
    assert info.main(["--require-stock"], Stock()) == 7


def stock_branch(setup: str) -> str:
    """remote_setup.sh's lines that run only without /opt/cool."""
    blocks = re.findall(r'^if \[ "\$MODE" = stock \]; then\n(.*?)\nelse$', setup, re.M | re.S)
    assert len(blocks) == 3  # packages, Python, the final OpenCV check
    return "\n".join(blocks)


def test_remote_setup_stock_mode_without_cool(tmp_path):
    """Without /opt/cool: Python 3.12 from Amazon Linux's repositories in a venv, requirements.lock's
    opencv-python-headless installed, no COOL library path, Caddy and FFmpeg pinned by checksum, and the stock check
    in place of the COOL proof."""
    setup = (DEPLOY / "remote_setup.sh").read_text()
    assert '[ -d /opt/cool ] || { MODE=stock; PY="$APP/venv/bin/python"; COOL_LIB=""; }' in setup
    stock = stock_branch(setup)
    assert "apt-get" not in stock and re.search(r"^dnf install -y -q python3\.12 ", stock, re.M)
    assert 'python3.12 -m venv "$APP/venv"' in stock
    assert re.search(r'cv2_info\.py" --require-stock', stock) and "--require-cool" not in stock
    assert re.search(r'cv2_info\.py" --require-cool', setup)  # the COOL path keeps its proof

    # the stock requirements keep the lock's OpenCV and drop only the test tools
    keep = re.search(r"^(grep -vE '.*' \"\$SRC/requirements\.in\") > /tmp/watchful-requirements\.txt$", stock, re.M)
    kept = subprocess.run(["bash", "-c", keep.group(1), "-"], capture_output=True, text=True, check=True,
                          env={**os.environ, "SRC": str(ROOT)}).stdout.splitlines()
    assert "opencv-python-headless==5.0.0.93" in kept
    assert "opencv-python-headless==5.0.0.93" in (ROOT / "requirements.lock").read_text().splitlines()
    assert not [line for line in kept if re.match(r"(pytest|moto|cfn-lint)\b", line)]
    assert "cp \"$SRC/requirements.lock\" /tmp/watchful-constraints.txt" in stock

    # no COOL library path in the instance's settings
    env_file, _ = instance_env_file(tmp_path)
    assert "LD_LIBRARY_PATH=/opt/cool/lib" in env_file.read_text()
    drop = re.search(r"^\[ -n \"\$COOL_LIB\" \] \|\| sed -i '(/\^LD_LIBRARY_PATH=/d)' /etc/watchful/watchful\.env", setup,
                     re.M)
    left = subprocess.run(["sed", drop.group(1), env_file], capture_output=True, text=True, check=True).stdout
    assert "LD_LIBRARY_PATH" not in left and "OPENCV_FFMPEG_CAPTURE_OPTIONS" in left
    assert 'env ${COOL_LIB:+LD_LIBRARY_PATH="$COOL_LIB"} "$PY"' in setup

    # pinned downloads, and Caddy's unit runs the binary where the setup puts it
    assert re.search(r"^CADDY_SHA512=[0-9a-f]{128}$", setup, re.M)
    assert re.search(r"^FFMPEG_SHA256=[0-9a-f]{64}$", setup, re.M)
    assert 'sha512sum -c -' in stock and 'sha256sum -c -' in stock and "tar -xzf /tmp/caddy.tgz -C /usr/bin caddy" in stock
    unit = (DEPLOY / "caddy.service").read_text()
    assert "ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile" in unit and "User=caddy" in unit
    assert 'install -m 644 "$SRC/deploy/caddy.service" /etc/systemd/system/caddy.service' in stock


def fake_proc(root: Path, rows: dict[int, tuple[int, str, int]], mem_total_kb: int = 8 * 1024 * 1024) -> Path:
    for pid, (ppid, cmd, rss_kb) in rows.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
        (d / "status").write_text(f"Name:\tpython\nPPid:\t{ppid}\nVmRSS:\t{rss_kb} kB\n")
    (root / "meminfo").write_text(f"MemTotal:       {mem_total_kb} kB\nMemFree:        1 kB\n")
    return root


def test_check_memory_counts_the_app_its_camera_processes_and_the_job_worker(tmp_path, capsys):
    check = load_deploy_module("check_memory")
    proc = fake_proc(tmp_path / "proc", {
        100: (1, "python -m uvicorn watchful.api:create_app --factory --port 8000", 1000 * 1024),
        101: (100, "python -c from multiprocessing.spawn import spawn_main --multiprocessing-fork", 300 * 1024),
        102: (101, "ffmpeg -i rtsp://cam", 50 * 1024),  # a grandchild counts too
        200: (1, "python -m watchful.worker", 200 * 1024),
        300: (1, "/usr/bin/caddy run", 80 * 1024)})
    rows = check.processes(proc=str(proc))
    assert [pid for pid, _, _ in rows] == [100, 101, 102, 200]
    assert sum(r[2] for r in rows) == 1550
    assert [pid for pid, _, _ in check.processes([101], proc=str(proc))] == [101, 102]
    assert check.default_budget_mb(None, proc=str(proc)) == pytest.approx(8192 * check.SHARE_OF_MEMORY)
    assert check.default_budget_mb(SimpleNamespace(resources=SimpleNamespace(memory_budget_mb=3000))) == 3000
    assert check.main(["--budget-mb", "2000", "--wait-s", "0", "--proc", str(proc)]) == 0
    assert check.main(["--budget-mb", "1000", "--wait-s", "0", "--proc", str(proc)]) == 1
    assert "memory: 1550 MB in 4 processes, budget 1000 MB: OVER" in capsys.readouterr().out


# ---- the web server and the files agree ----

def caddy_log_rule() -> tuple[str, str]:
    caddy = (DEPLOY / "Caddyfile").read_text()
    rules = re.findall(r"^\s*request>uri regexp (\S+) (\S+)$", caddy, re.M)
    assert len(rules) == 1
    pattern, replacement = rules[0]
    return pattern, re.sub(r"\$\{(\d+)\}", r"\\g<\1>", replacement)


def test_access_log_keeps_no_tokens_or_secrets():
    """Sign in links, camera device links, invites and the Telegram webhook carry a token in the path, signed media
    links carry one in the query: Caddy's access log (90 days in CloudWatch) keeps neither, nor secret headers."""
    caddy = (DEPLOY / "Caddyfile").read_text()
    for header in ("Cookie", "Authorization", "X-Telegram-Bot-Api-Secret-Token", "Sec-Websocket-Protocol", "Referer"):
        assert re.search(rf"^\s*request>headers>{header} delete$", caddy, re.M), header
    assert re.search(r"^\s*resp_headers>Set-Cookie delete$", caddy, re.M)
    pattern, python = caddy_log_rule()

    def logged(uri: str) -> str:
        return re.sub(pattern, python, uri)

    token = "Zk3" * 15
    for prefix in ("/auth/link/", "/cam/", "/ws/cam/", "/telegram/webhook/", "/join/", "/api/invites/"):
        for uri in (f"{prefix}{token}", f"{prefix}{token}/ack", f"{prefix}{token}?a=1"):
            assert token not in logged(uri) and logged(uri).startswith(prefix), uri
    assert logged(f"/api/blobs/ws1/a.jpg?exp=1&sig={token}") == "/api/blobs/ws1/a.jpg?-"
    for uri in ("/api/cameras/cam_1", "/ws/cameras/cam_1", "/healthz", "/assets/index-3f2a.js", "/app/logs/staff"):
        assert logged(uri) == uri


def test_caddy_proxies_sockets_and_leaves_the_policy_to_the_app():
    """Live views and camera pushes are WebSockets under /ws/: proxied without compression and kept open across a
    reload. The app sends the Content Security Policy (it allows wss to this host and blob: media); Caddy adds none,
    so there is only one, built from PUBLIC_BASE_URL, which remote_setup.sh sets to this domain."""
    caddy = (DEPLOY / "Caddyfile").read_text()
    sockets = re.search(r"handle /ws/\* \{(.*?)\n\t\}", caddy, re.S)
    assert sockets and "reverse_proxy 127.0.0.1:8000" in sockets.group(1) and "encode" not in sockets.group(1)
    assert "stream_close_delay" in sockets.group(1)
    assert "Content-Security-Policy" not in caddy
    assert "PUBLIC_BASE_URL=https://$DOMAIN" in (DEPLOY / "remote_setup.sh").read_text()


def test_the_apps_policy_allows_sockets_and_blob_media():
    """Live views and camera pushes open wss:// sockets to PUBLIC_BASE_URL's host and pages show blob: media; the
    app's Content Security Policy has to allow both, since Caddy adds none."""
    from watchful.api.headers import policy
    csp = policy(Settings(session_secret="y", public_base_url=ORIGIN, _env_file=None))
    assert f"wss://{DOMAIN}" in csp and "blob:" in csp.split("img-src")[1].split(";")[0]
    assert "blob:" in csp.split("media-src")[1].split(";")[0] and "frame-ancestors 'none'" in csp


def test_caddy_validates_the_caddyfile():
    if shutil.which("caddy") is None:
        pytest.skip("caddy is not installed (remote_setup.sh validates it on the instance before any restart)")
    text = (DEPLOY / "Caddyfile").read_text().replace("@DOMAIN@", DOMAIN).replace("@ACME_EMAIL@", "ops@example.com")
    result = subprocess.run(["caddy", "validate", "--adapter", "caddyfile", "--config", "/dev/stdin"], input=text,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cloudflare_is_trusted_only_behind_the_ioflood_tunnel():
    tunnel = (DEPLOY / "ioflood.env.example").read_text()
    assert re.search(r"^TRUST_CLOUDFLARE=true$", tunnel, re.M) and "tunnel" in tunnel.lower()
    for aws in ("params.env.example", "remote_setup.sh", "cloudformation.yaml"):
        assert "TRUST_CLOUDFLARE" not in (DEPLOY / aws).read_text(), aws  # AWS: Caddy is the only way in
    assert "TRUST_CLOUDFLARE" in stack_decided()  # and deploy.sh refuses it (test below)
    example = ROOT / ".env.example"
    if not example.exists():
        pytest.skip(".env.example is not in this copy (the ioflood sync leaves out .env files)")
    values = re.findall(r"^#? ?TRUST_CLOUDFLARE=(.*)$", example.read_text(), re.M)
    assert values in ([], ["false"])


def test_env_example_lists_every_setting_with_its_default():
    from watchful.config import Settings
    example = ROOT / ".env.example"
    if not example.exists():
        pytest.skip(".env.example is not in this copy (the ioflood sync leaves out .env files)")
    lines = re.findall(r"^(# )?([A-Z][A-Z0-9_]*)=(.*)$", example.read_text(), re.M)
    assert len({name for _, name, _ in lines}) == len(lines)
    assert {name for _, name, _ in lines} == {name.upper() for name in Settings.model_fields}

    def shown(value) -> str:
        if isinstance(value, list):
            return ",".join(value)
        return str(value).lower() if isinstance(value, bool) else str(value)

    for commented, name, value in lines:
        field = Settings.model_fields[name.lower()]
        if field.is_required():
            assert (commented, value) == ("", ""), name
        elif field.default is None or field.default == []:
            assert (commented, value) == ("# ", ""), name
        else:
            assert (commented, value) == ("", shown(field.default)), name


def stack_decided() -> set[str]:
    return set(re.search(r'^STACK_DECIDED="([^"]*)"', (DEPLOY / "deploy.sh").read_text(), re.M | re.S)
               .group(1).replace("\\\n", " ").split())


def instance_env_file(tmp_path) -> tuple[Path, list[str]]:
    """remote_setup.sh's /etc/watchful/watchful.env rendered with test values, and the names in its stack section."""
    setup = (DEPLOY / "remote_setup.sh").read_text()
    body = re.search(r"^cat > /etc/watchful/watchful.env <<EOF\n(.*?)\nEOF$", setup, re.M | re.S).group(1)
    stack_part = body.split("# defaults the owner may change")[0]
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", stack_part, re.M)
    script = tmp_path / "render.sh"
    script.write_text(f"cat <<EOF\n{body}\nEOF\n")
    values = {"STACK": STACK, "REGION": MUMBAI, "BUCKET": BUCKET, "BEDROCK_MODEL": f"global.{MODEL}",
              "DOMAIN": DOMAIN, "DATA": "/var/lib/watchful", "APP": "/opt/watchful", "COOL_LIB": "/opt/cool/lib"}
    rendered = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True,
                              env={**os.environ, **values}).stdout
    path = tmp_path / "watchful.env"
    path.write_text(rendered)
    return path, names


def test_instance_settings_force_rtsp_over_tcp_for_systemd_and_shell(tmp_path):
    """The security group lets no UDP out and FFmpeg tries RTSP over UDP first, so the instance's settings tell
    OpenCV's FFmpeg to use TCP. systemd (EnvironmentFile) and the watchful-python wrapper (bash `. file`) must read
    the same value from the file."""
    env_file, _ = instance_env_file(tmp_path)
    shell = 'set -a; . "$1"; printf %s "$OPENCV_FFMPEG_CAPTURE_OPTIONS"'
    sourced = subprocess.run(["bash", "-c", shell, "-", env_file], capture_output=True, text=True, check=True)
    assert sourced.stdout == "rtsp_transport;tcp" and sourced.stderr == ""
    for line in env_file.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, value = line.split("=", 1)
        quoted = len(value) > 1 and value[0] == value[-1] == '"'
        plain = value[1:-1] if quoted else value
        assert quoted or not re.search(r"[;\s$`'\"\\]", value), line  # anything special is double quoted
        got = subprocess.run(["bash", "-c", f'set -a; . "$1"; printf %s "${name}"', "-", env_file],
                             capture_output=True, text=True, check=True).stdout
        assert got == plain, line  # what systemd reads (quotes removed) is what the shell reads


def test_every_required_setting_reaches_the_instance():
    """A required setting with no default must come from the stack's file or be generated into SSM by deploy.sh;
    nothing on AWS asks for the old demo password."""
    from watchful.config import Settings
    deploy = (DEPLOY / "deploy.sh").read_text()
    generated = set(re.search(r'^GENERATED="([^"]*)"', deploy, re.M).group(1).split())
    required = {name.upper() for name, field in Settings.model_fields.items() if field.is_required()}
    assert required and required <= generated | stack_decided(), required - generated - stack_decided()
    assert {"SESSION_SECRET", "DATA_KEY"} <= required
    params = (DEPLOY / "params.env.example").read_text()
    assert all(re.search(rf"^{name}=$", params, re.M) for name in generated)
    for path in DEPLOY.rglob("*"):
        if path.is_file() and path.suffix not in (".mp4", ".pyc"):
            assert "DEMO_PASSWORD" not in path.read_text() and "DEMO_USER" not in path.read_text(), path


def test_deploy_refuses_names_the_stack_sets(fake_aws, tmp_path):
    _, names = instance_env_file(tmp_path)
    decided = stack_decided()
    assert names and all(n in decided or n.startswith("AWS_") for n in names), set(names) - decided
    assert "AI_PROVIDER" not in decided  # an owner's choice, which SSM may change
    params = tmp_path / "params.env"
    base = params.read_text()
    for name in ("STORAGE", "TRUST_CLOUDFLARE", "PUBLIC_BASE_URL", "RUN_JOBS_IN_APP", "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                 "AWS_ACCESS_KEY_ID", "AWS_REGION"):
        params.write_text(base + f"{name}=x\n")
        result, calls = fake_aws("--dry-run")
        assert result.returncode != 0 and f"{name} is set by the stack" in result.stderr, name
        assert calls == [] and "+ aws" not in result.stdout, name
    params.write_text(base)
    assert fake_aws("--dry-run")[0].returncode == 0


def test_deploy_files_agree():
    for script in ("deploy.sh", "remote_setup.sh", "baseline_venv.sh"):
        result = subprocess.run(["bash", "-n", str(DEPLOY / script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    setup = (DEPLOY / "remote_setup.sh").read_text()
    units = {p.stem: p.read_text() for p in (DEPLOY / "systemd").glob("*.service")}
    assert set(units) == {"watchful-app", "watchful-worker", "watchful-litestream"}
    assert "for unit in watchful-app watchful-worker watchful-litestream; do" in setup
    assert "systemctl restart watchful-app watchful-worker watchful-litestream" in setup

    # the app: one uvicorn process on 8000 (camera processes are its children), started through the SSM loader
    app = re.search(r"^ExecStart=(.*)$", units["watchful-app"], re.M).group(1).split()
    assert app[:3] == ["@PYTHON@", "@APP@/current/deploy/ssm_env.py", "--"]
    assert app[3:6] == ["@PYTHON@", "-m", "uvicorn"]
    module, factory = app[6].split(":")
    assert callable(getattr(importlib.import_module(module), factory))
    assert {"--factory", "--workers", "1", "--port", "8000", "--host", "127.0.0.1"} <= set(app)
    assert "KillMode=control-group" in units["watchful-app"]
    assert "reverse_proxy 127.0.0.1:8000" in (DEPLOY / "Caddyfile").read_text()
    worker = re.search(r"^ExecStart=(.*)$", units["watchful-worker"], re.M).group(1).split()
    assert worker == ["@PYTHON@", "@APP@/current/deploy/ssm_env.py", "--", "@PYTHON@", "-m", "watchful.worker"]
    assert importlib.util.find_spec("watchful.worker") is not None
    assert "RUN_JOBS_IN_APP=false" in setup  # the worker unit is the only job worker

    data = re.search(r"^DATA=(\S+)", setup, re.M).group(1)
    logs = re.search(r"^LOGS=(\S+)", setup, re.M).group(1)
    litestream = yaml.safe_load((DEPLOY / "litestream.yml").read_text())
    assert litestream["dbs"][0]["path"] == f"{data}/watchful.db"
    assert "DATABASE_URL=sqlite:///$DATA/watchful.db" in setup
    assert litestream["dbs"][0]["replica"]["path"].startswith("db/")
    for unit in units.values():
        assert "EnvironmentFile=/etc/watchful/watchful.env" in unit and f"append:{logs}/" in unit
    agent = json.loads((DEPLOY / "cloudwatch-agent.json").read_text())
    files = {f["file_path"] for f in agent["logs"]["logs_collected"]["files"]["collect_list"]}
    assert {f"{logs}/app.log", f"{logs}/worker.log", f"{logs}/litestream.log", f"{logs}/deploy.log",
            "/var/log/caddy/access.log"} == files
    assert (DEPLOY / "logrotate.conf").read_text().startswith(f"{logs}/*.log")

    # names the stack and the app read come from brand.json, never a literal
    for path in DEPLOY.rglob("*"):
        if path.is_file() and path.suffix not in (".mp4", ".pyc"):
            text = path.read_text().lower()
            assert BRAND["name"].lower() not in text and "packsure" not in text, path


def test_the_web_build_gets_the_arm_bundlers_the_lockfile_leaves_out():
    """web/package-lock.json is made on x86, so npm ci on a Graviton instance has no rollup or esbuild binary for
    arm64 and vite build stops ("Cannot find module @rollup/rollup-linux-arm64-gnu"). The setup adds both at the
    versions the lockfile pins."""
    setup = (DEPLOY / "remote_setup.sh").read_text()
    build = setup[setup.index('say "web build"'):setup.index('say "settings"')]
    assert "@rollup/rollup-linux-arm64-gnu@" in build and "@esbuild/linux-arm64@" in build
    assert build.index("npm ci") < build.index("@rollup/rollup-linux-arm64-gnu@") < build.index("npm run build")
    assert "require('rollup/package.json').version" in build and "require('esbuild/package.json').version" in build


def test_caddy_owns_its_access_log_after_the_root_validate():
    """Caddy writes /var/log/caddy/access.log (Caddyfile). `caddy validate` runs as root during setup and opens that
    file, leaving it root's with mode 600, so the caddy service then exits on start ("opening log writer") and the
    site never comes up. The setup gives the folder back to caddy after validating."""
    assert "output file /var/log/caddy/access.log" in (DEPLOY / "Caddyfile").read_text()
    setup = (DEPLOY / "remote_setup.sh").read_text()
    validate = setup.index("caddy validate --config /etc/caddy/Caddyfile")
    assert setup.index("chown -R caddy:caddy /var/log/caddy", validate) < setup.index("systemctl", validate)
