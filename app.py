"""CDK App — Database (Repositorio Digital UNIPAZ).

Aurora Serverless v2 (PostgreSQL) con Data API, una sola instancia. La red
(VPC, subnets aisladas, SG de Lambda) viene de `account-level-resources` por
SSM. Publica en SSM el ARN del cluster, el ARN del secreto master y el nombre
de la base para que los backends (carga_documentos / consulta_documentos) los
consuman.

Cadena en cdk.json: `python validate_env.py && python app.py`.
"""
import aws_cdk as cdk

from configs.config import load_config
from configs.env_resolver import prefixed_name, read_env
from stacks.aurora_stack import AuroraStack
from stacks.secret_stack import SecretStack


app = cdk.App()

env_name = read_env()
app_config = load_config("app_config.yaml")
config = load_config("aurora.yaml")
app_settings = app_config["app_settings"]

env = cdk.Environment(
    account=app_settings["account_id"],
    region=app_settings["region"],
)

# Tags globales (FinOps) a nivel de app.
cdk.Tags.of(app).add("project", "Unipaz")
cdk.Tags.of(app).add("environment", env_name)
for key, value in app_settings.get("tags", {}).items():
    cdk.Tags.of(app).add(key, value)

aurora_stack = AuroraStack(
    app,
    prefixed_name(env_name, "Unipaz-Database-Aurora"),
    env_name=env_name,
    config=config,
    env=env,
    description="Aurora Serverless v2 (PostgreSQL) - Repositorio UNIPAZ",
)

# Usuarios adicionales: solo se crea el stack si hay secretos configurados.
if config.get("secrets"):
    secret_stack = SecretStack(
        app,
        prefixed_name(env_name, "Unipaz-Database-Secrets"),
        env_name=env_name,
        config=config,
        env=env,
        description="Secretos de usuarios de base de datos - Repositorio UNIPAZ",
    )
    secret_stack.add_stack_dependency(aurora_stack)

app.synth()
