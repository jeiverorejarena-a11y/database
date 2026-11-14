"""Tests del AuroraStack (CDK assertions sobre el template sintetizado).

Ejecutar desde la raíz del repo:
    python -m unittest discover -s tests -v

No requieren credenciales AWS ni `.cdk-env`: el YAML se carga resolviendo
{{env}} directamente y el synth no hace lookups.
"""
import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

from configs.config import _resolve_placeholders  # noqa: E402
from stacks.aurora_stack import AuroraStack  # noqa: E402

ENV = cdk.Environment(account="041391475423", region="us-east-2")


def _config(env_name: str) -> dict:
    raw = yaml.safe_load((ROOT / "configs" / "aurora.yaml").read_text(encoding="utf-8"))
    return _resolve_placeholders(raw, env_name)


def _synth(env_name: str):
    app = cdk.App()
    stack = AuroraStack(app, "TestAurora", env_name=env_name, config=_config(env_name), env=ENV)
    return app, Template.from_stack(stack)


class AuroraStackProdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.t = _synth("prod")

    def test_single_serverless_instance_no_reader_no_proxy(self):
        self.t.resource_count_is("AWS::RDS::DBCluster", 1)
        self.t.resource_count_is("AWS::RDS::DBInstance", 1)
        self.t.resource_count_is("AWS::RDS::DBProxy", 0)
        self.t.has_resource_properties(
            "AWS::RDS::DBInstance",
            {"DBInstanceClass": "db.serverless", "PubliclyAccessible": True},
        )

    def test_tls_enforced(self):
        self.t.has_resource_properties(
            "AWS::RDS::DBClusterParameterGroup",
            {"Parameters": Match.object_like({"rds.force_ssl": "1"})},
        )

    def test_client_ip_is_restricted(self):
        self.t.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": Match.array_with(
                    [Match.object_like({"CidrIp": "190.69.47.2/32", "FromPort": 5432})]
                ),
            },
        )
        template = self.t.to_json()
        for res in template["Resources"].values():
            if res["Type"] == "AWS::EC2::SecurityGroup":
                for rule in res["Properties"].get("SecurityGroupIngress", []):
                    self.assertNotEqual(rule.get("CidrIp"), "0.0.0.0/0")

    def test_open_to_world_is_rejected(self):
        cfg = _config("prod")
        cfg["security_group"]["ingress_rules"].append(
            {"description": "mal", "port": 5432, "cidr": "0.0.0.0/0"}
        )
        with self.assertRaises(Exception):
            AuroraStack(cdk.App(), "Bad", env_name="prod", config=cfg, env=ENV)

    def test_cluster_minimal_serverless_v2_with_data_api(self):
        self.t.has_resource_properties(
            "AWS::RDS::DBCluster",
            {
                "Engine": "aurora-postgresql",
                "EngineVersion": "17.9",
                "DatabaseName": "unipaz_repositorio",
                "EnableHttpEndpoint": True,
                "StorageEncrypted": True,
                "BackupRetentionPeriod": 1,
                "ServerlessV2ScalingConfiguration": {
                    "MinCapacity": 0,
                    "MaxCapacity": 1,
                    "SecondsUntilAutoPause": 300,
                },
            },
        )

    def test_prod_protection(self):
        self.t.has_resource(
            "AWS::RDS::DBCluster",
            {"DeletionPolicy": "Snapshot", "Properties": Match.object_like({"DeletionProtection": True})},
        )

    def test_network_comes_from_ssm(self):
        for name in (
            "/unipaz/vpc/id",
            "/unipaz/vpc/subnet-public-1/id",
            "/unipaz/vpc/subnet-public-2/id",
            "/unipaz/lambda/sg/default",
        ):
            params = self.t.find_parameters("*", {"Default": name})
            self.assertEqual(len(params), 1, f"falta parámetro SSM {name}")

    def test_sg_ingress_postgres(self):
        self.t.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "GroupName": "prod-unipaz-repositorio-db-sg",
                "SecurityGroupIngress": Match.array_with(
                    [Match.object_like({"CidrIp": "10.20.0.0/16", "FromPort": 5432, "ToPort": 5432})]
                ),
            },
        )

    def test_ssm_contract(self):
        for key in ("cluster-endpoint", "cluster-arn", "master-secret-arn", "db-name", "security-group-id"):
            self.t.has_resource_properties(
                "AWS::SSM::Parameter",
                {"Name": f"/unipaz/prod/repositorio/database/{key}"},
            )

    def test_no_context_lookups(self):
        manifest = json.loads(
            (Path(self.app.synth().directory) / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual([m["key"] for m in manifest.get("missing", [])], [])


class AuroraStackDevTest(unittest.TestCase):
    def test_dev_is_destroyable(self):
        _, t = _synth("dev")
        t.has_resource(
            "AWS::RDS::DBCluster",
            {"DeletionPolicy": "Delete", "Properties": Match.object_like({"DeletionProtection": False})},
        )
        t.has_resource_properties(
            "AWS::SSM::Parameter", {"Name": "/unipaz/dev/repositorio/database/cluster-arn"}
        )


if __name__ == "__main__":
    unittest.main()
