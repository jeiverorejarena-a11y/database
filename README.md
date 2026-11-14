# Database — Aurora Serverless v2 (PostgreSQL) · Repositorio UNIPAZ

CDK app (Python) que crea el cluster **Aurora Serverless v2 PostgreSQL 17.9**
con la configuración mínima posible y publica sus datos de conexión en SSM.
Adaptado de `brands-rds`.

- **1 sola instancia** `db.serverless` (writer). Sin reader, sin RDS Proxy.
- **0–1 ACU** con auto-pause a los 5 min sin actividad (sin uso, solo se paga
  storage).
- **Data API** habilitada: las Lambdas consultan por HTTPS con el ARN del
  cluster y el ARN del secreto, sin estar dentro de la VPC ni usar drivers.
- **No es pública**: vive en las subnets aisladas de la VPC compartida.
- Cifrado en reposo (clave administrada `aws/rds`), backup de 1 día.

## Dependencias

Requiere desplegado antes **`account-level-resources`** (red compartida). Lee de
SSM en deploy:

| Parámetro | Uso |
|---|---|
| `/unipaz/vpc/id` | VPC del security group |
| `/unipaz/vpc/subnet-isolated-1/id`, `-2` | Subnet group (us-east-2a, us-east-2b) |
| `/unipaz/lambda/sg/default` | Ingreso 5432 desde Lambdas en VPC (futuro) |

## Salidas en SSM

| Parámetro | Valor |
|---|---|
| `/unipaz/<env>/repositorio/database/cluster-arn` | `resourceArn` para Data API |
| `/unipaz/<env>/repositorio/database/master-secret-arn` | `secretArn` para Data API |
| `/unipaz/<env>/repositorio/database/db-name` | `unipaz_repositorio` |
| `/unipaz/<env>/repositorio/database/cluster-endpoint` | Endpoint del writer |
| `/unipaz/<env>/repositorio/database/security-group-id` | SG del cluster |

## Entorno

`validate_env.py` resuelve `dev` o `prod` (rama `main` o `--context env=prod`
→ `prod`). En `prod` el cluster tiene **deletion protection** y deja
**snapshot final** al borrarse; en `dev` se destruye sin snapshot.

## Configuración

Todo en `configs/aurora.yaml`: versión, ACU min/max, auto-pause, backup,
reglas del security group y usuarios adicionales (`secrets`).

## Comandos

```powershell
python -m unittest discover -s tests -v   # tests (sin credenciales)
cdk.cmd synth
cdk.cmd diff
cdk.cmd deploy                            # ~10-15 min (creación del cluster)
```

## Probar la conexión (Data API)

```powershell
$P = "/unipaz/prod/repositorio/database"
$ARN = aws ssm get-parameter --name "$P/cluster-arn" --query Parameter.Value --output text
$SEC = aws ssm get-parameter --name "$P/master-secret-arn" --query Parameter.Value --output text
aws rds-data execute-statement --resource-arn $ARN --secret-arn $SEC `
  --database unipaz_repositorio --sql "SELECT version()"
```

Si el cluster está pausado, la primera llamada devuelve
`DatabaseResumingException`; reintentar a los ~15 s.

También se puede consultar desde la consola: **RDS → Query editor**, usando el
secreto `<env>-unipaz-repositorio/master-credentials`.

## Costos de referencia (us-east-2)

- Pausado: $0 de cómputo; storage ~$0.10/GB-mes; secreto $0.40/mes.
- Activo: ~$0.12 por ACU-hora (máximo 1 ACU → techo ~$0.12/h).
