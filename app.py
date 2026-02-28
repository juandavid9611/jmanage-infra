#!/usr/bin/env python3
import os

import aws_cdk as cdk

from jmanage_infra.jmanage_infra_stack import JmanageInfraStack


app = cdk.App()
env_name = os.environ.get("CDK_ENV", "dev")

if env_name == "prod":
    JmanageInfraStack(app, "JmanageInfraStack", "prod")
else:
    JmanageInfraStack(app, "JmanageInfraStack-dev", "dev")

app.synth()
