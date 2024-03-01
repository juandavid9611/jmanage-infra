#!/usr/bin/env python3
import os

import aws_cdk as cdk

from jmanage_infra.jmanage_infra_stack import JmanageInfraStack


app = cdk.App()
is_prod = False
if is_prod:
    JmanageInfraStack(app, "JmanageInfraStack")
else:
    JmanageInfraStack(app, "JmanageInfraStack-dev")

app.synth()
