from aws_cdk import (
    # Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    CfnOutput,
    # aws_sqs as sqs,
)
from constructs import Construct

class JmanageInfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # The code that defines your stack goes here

        # example resource
        # queue = sqs.Queue(
        #     self, "JmanageInfraQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )

        payment_request_table = dynamodb.Table(self, "PaymentRequest",
            partition_key=dynamodb.Attribute(
                name="payment_request_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
        )

        payment_request_table.add_global_secondary_index(
            index_name="user_index",
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_time",
                type=dynamodb.AttributeType.NUMBER
            ),
        )

        api = lambda_.Function(self, "API",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("../jmanage-api/lambda_function.zip"),
            handler="app.handler",
            environment={
                "PAYMENT_REQUEST_TABLE_NAME": payment_request_table.table_name,
            }
        )

        function_url = api.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                # Allow this to be called from websites on https://example.com.
                # Can also be ['*'] to allow all domain.
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.ALL],
                allowed_headers=["*"],
            )
        )

        CfnOutput(self, "APIUrl",
            value=function_url.url,
            description="URL of the API function"
        )

        CfnOutput(self, "PaymentRequestTableName",
            value=payment_request_table.table_name,
            description="Name of the PaymentRequest table")

        payment_request_table.grant_read_write_data(api);


