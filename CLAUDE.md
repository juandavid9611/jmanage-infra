# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source .venv/bin/activate
cdk synth        # Preview the CloudFormation template
cdk diff         # Compare current code with deployed stack
cdk deploy       # Deploy to AWS
cdk ls           # List stacks
```

## Architecture

Single CDK stack (`jmanage_infra/jmanage_infra_stack.py`) that provisions all AWS resources. The stack accepts an `env_name` parameter (`dev` or `prod`) which controls environment-specific config (log level, Slack webhook).

**Resources provisioned:**
- DynamoDB tables — one per entity (User, Tour, Calendar, Workspace, Membership, Product, Order, Account, File, Tournament, TournamentTeam, TournamentPlayer, TournamentMatch, TournamentMatchEvent). Each has an `account_id_index` GSI for tenant-scoped queries.
- AWS Lambda — Python 3.12, loaded from `../jmanage-api/lambda_function.zip`
- Cognito User Pool + two clients (web, api)
- S3 bucket (`jmanage-bucket`)

## Deployment flow

`jmanage-api` and `jmanage-infra` must be sibling directories. CDK reads the Lambda zip from `../jmanage-api/lambda_function.zip`.

1. Make changes in `jmanage-api/`
2. `cd ../jmanage-api && ./package_for_lambda.sh` (requires Docker)
3. `cd ../jmanage-infra && cdk deploy`
