"""Secret Stack — credenciales de usuarios adicionales de la base.

Vacío por ahora (`secrets: []` en configs/aurora.yaml). Cuando se necesiten
usuarios de aplicación con menos privilegios que el master, se agregan ahí.
Nota: el secreto solo guarda la credencial; el usuario debe crearse dentro de
PostgreSQL (CREATE ROLE ...).
"""
from typing import Any, Dict

from aws_cdk import (
    Stack,
    aws_rds as rds,
    aws_ssm as ssm,
)
from constructs import Construct

from configs.env_resolver import ssm_param_name

COMPONENT = "database"


class SecretStack(Stack):
    """Secretos de usuarios de base de datos."""

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

        for secret_def in config.get("secrets", []):
            secret = rds.DatabaseSecret(
                self,
                secret_def["username"],
                username=secret_def["username"],
                dbname=db_name,
                secret_name=secret_def["secret_name"],
                exclude_characters="/@\" '\\",
            )

            ssm.StringParameter(
                self,
                f"{secret_def['username']}-SecretArn",
                parameter_name=ssm_param_name(
                    env_name, COMPONENT, f"secrets/{secret_def['username']}/arn"
                ),
                string_value=secret.secret_arn,
                description=secret_def.get("description", ""),
            )
