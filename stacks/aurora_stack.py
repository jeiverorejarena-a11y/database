"""Aurora Serverless v2 Stack (PostgreSQL) — Repositorio Digital UNIPAZ.

Adaptado de `brands-rds`:
  - La red (VPC, subnets aisladas, SG de Lambda) se lee de SSM, publicada por
    `account-level-resources`. Se resuelve en deploy (sin lookups en synth).
  - Una sola instancia `db.serverless` (writer). Sin reader ni RDS Proxy.
  - Serverless v2 con min 0 ACU (auto-pause) y Data API habilitada: las Lambdas
    consultan por HTTPS sin entrar a la VPC.
  - En subnets públicas con `publicly_accessible` (configurable) para conectar
    clientes locales (DBeaver). El acceso lo restringe el SG a IPs /32 y el
    parameter group exige TLS (`rds.force_ssl=1`).
  - Nombres prefijados por entorno; salidas en SSM bajo
    /unipaz/<env>/repositorio/database/*.
"""
from typing import Any, Dict

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
)
from constructs import Construct

from configs.env_resolver import is_prod, ssm_param_name

COMPONENT = "database"


class AuroraStack(Stack):
    """Aurora Serverless v2 — single region, una sola instancia."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        config: Dict[str, Any],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        db_name = config["db_name"]
        prefix = config["resource_prefix"]
        net = config["network"]

        # ── Red desde SSM (account-level-resources) ────────────────────────
        vpc_id = ssm.StringParameter.value_for_string_parameter(self, net["vpc_id_ssm"])
        vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "Vpc",
            vpc_id=vpc_id,
            availability_zones=net["availability_zones"],
        )
        subnet_ids = [
            ssm.StringParameter.value_for_string_parameter(self, param)
            for param in net["subnet_ssm_params"]
        ]

        # ── Security Group ─────────────────────────────────────────────────
        sg_config = config["security_group"]
        self.db_sg = ec2.SecurityGroup(
            self,
            "AuroraSG",
            vpc=vpc,
            security_group_name=sg_config["name"],
            description=sg_config.get("description", ""),
            allow_all_outbound=True,
        )

        for rule in sg_config.get("ingress_rules", []):
            if "source_security_group_ssm" in rule:
                sg_id = ssm.StringParameter.value_for_string_parameter(
                    self, rule["source_security_group_ssm"]
                )
                peer = ec2.Peer.security_group_id(sg_id)
            elif "source_security_group_id" in rule:
                peer = ec2.Peer.security_group_id(rule["source_security_group_id"])
            elif "cidr" in rule:
                if rule["cidr"] in ("0.0.0.0/0", "::/0"):
                    raise ValueError(
                        "No se permite abrir la base a todo internet "
                        f"(regla '{rule.get('description', '')}'). Usa una IP /32."
                    )
                peer = ec2.Peer.ipv4(rule["cidr"])
            else:
                raise ValueError(
                    "Cada ingress_rule debe indicar una fuente "
                    "(source_security_group_ssm | source_security_group_id | cidr)."
                )

            self.db_sg.add_ingress_rule(
                peer=peer,
                connection=ec2.Port.tcp(int(rule["port"])),
                description=rule.get("description", ""),
            )

        # ── Subnet group (subnets aisladas) ────────────────────────────────
        subnet_group = rds.CfnDBSubnetGroup(
            self,
            "SubnetGroup",
            db_subnet_group_description=f"Subnet group for {prefix} Aurora",
            subnet_ids=subnet_ids,
        )

        # ── Credenciales master (secreto generado) ─────────────────────────
        self.master_secret = rds.DatabaseSecret(
            self,
            "MasterCredentials",
            username=config["db_username"],
            dbname=db_name,
            secret_name=f"{prefix}/master-credentials",
        )

        # ── Parameter group ────────────────────────────────────────────────
        param_group = rds.CfnDBClusterParameterGroup(
            self,
            "ParameterGroup",
            family=config["parameter_group_family"],
            description=f"Parameter group for {prefix} cluster",
            parameters={
                "shared_preload_libraries": "pg_stat_statements",
                "log_min_duration_statement": "3000",
                "log_statement": "none",
                # La instancia es alcanzable desde internet: solo conexiones TLS.
                "rds.force_ssl": "1",
            },
        )

        # ── Aurora Cluster ─────────────────────────────────────────────────
        self.cluster = rds.CfnDBCluster(
            self,
            "Cluster",
            engine="aurora-postgresql",
            engine_version=str(config["engine_version"]),
            database_name=db_name,
            port=5432,
            db_cluster_parameter_group_name=param_group.ref,
            master_username=self.master_secret.secret_value_from_json(
                "username"
            ).unsafe_unwrap(),
            master_user_password=self.master_secret.secret_value_from_json(
                "password"
            ).unsafe_unwrap(),
            storage_encrypted=True,
            deletion_protection=is_prod(env_name),
            backup_retention_period=int(config.get("backup_retention_days", 1)),
            copy_tags_to_snapshot=True,
            serverless_v2_scaling_configuration=rds.CfnDBCluster.ServerlessV2ScalingConfigurationProperty(
                min_capacity=float(config["min_capacity"]),
                max_capacity=float(config["max_capacity"]),
                seconds_until_auto_pause=config.get("seconds_until_auto_pause"),
            ),
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[self.db_sg.security_group_id],
            enable_http_endpoint=True,  # Data API
        )
        # prod: snapshot final al borrar el cluster; dev: se destruye sin más.
        self.cluster.apply_removal_policy(
            RemovalPolicy.SNAPSHOT if is_prod(env_name) else RemovalPolicy.DESTROY
        )

        # ── Única instancia (writer: lecturas y escrituras) ────────────────
        self.writer = rds.CfnDBInstance(
            self,
            "WriterInstance",
            db_instance_class="db.serverless",
            engine="aurora-postgresql",
            db_cluster_identifier=self.cluster.ref,
            promotion_tier=0,
            auto_minor_version_upgrade=True,
            publicly_accessible=bool(config.get("publicly_accessible", False)),
        )
        self.writer.apply_removal_policy(RemovalPolicy.DESTROY)

        # Adjuntar el secreto master al cluster (Data API usa este secreto).
        secretsmanager.CfnSecretTargetAttachment(
            self,
            "SecretAttachment",
            secret_id=self.master_secret.secret_arn,
            target_id=self.cluster.ref,
            target_type="AWS::RDS::DBCluster",
        )

        # ── Parámetros SSM para los demás repos ────────────────────────────
        outputs = {
            "cluster-endpoint": (self.cluster.attr_endpoint_address, "Aurora cluster writer endpoint"),
            "cluster-arn": (self.cluster.attr_db_cluster_arn, "Aurora cluster ARN (Data API resourceArn)"),
            "master-secret-arn": (self.master_secret.secret_arn, "Master credentials secret ARN (Data API secretArn)"),
            "db-name": (db_name, "Database name"),
            "security-group-id": (self.db_sg.security_group_id, "Aurora cluster security group ID"),
        }
        for key, (value, description) in outputs.items():
            ssm.StringParameter(
                self,
                f"Param-{key}",
                parameter_name=ssm_param_name(env_name, COMPONENT, key),
                string_value=value,
                description=description,
            )

        CfnOutput(self, "ClusterArn", value=self.cluster.attr_db_cluster_arn)
        CfnOutput(self, "ClusterEndpoint", value=self.cluster.attr_endpoint_address)
        CfnOutput(self, "MasterSecretArn", value=self.master_secret.secret_arn)
