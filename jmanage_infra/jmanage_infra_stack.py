from aws_cdk import (
    # Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    CfnOutput,
    aws_cognito as cognito,
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
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
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

        user_table = dynamodb.Table(self, "User",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        pool = cognito.UserPool(
            self, "JmanageUserPool", 
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY, 
            auto_verify=cognito.AutoVerifiedAttrs(email=True), 
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_digits=True,
            ),
            deletion_protection=True,
            custom_attributes={
                "role": cognito.StringAttribute(mutable=True),
            }
        )
        pool_web_client = pool.add_client("jmanage-web")
        pool_api_client = pool.add_client("jmanage-api")

        api = lambda_.Function(self, "API",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("../jmanage-api/lambda_function.zip"),
            handler="app.handler",
            environment={
                "PAYMENT_REQUEST_TABLE_NAME": payment_request_table.table_name,
                "USER_TABLE_NAME": user_table.table_name,
                "USER_POOL_ID": pool.user_pool_id,
                "USER_POOL_API_CLIENT_ID": pool_api_client.user_pool_client_id,
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
        
        CfnOutput(self, "UserTableName",
            value=user_table.table_name,
            description="Name of the User table")
        
        CfnOutput(self, "UserPoolId",
            value=pool.user_pool_id,
            description="ID of the User Pool")
        
        CfnOutput(self, "UserPoolWebClientId",
            value=pool_web_client.user_pool_client_id,
            description="ID of the User Pool Client")
        
        CfnOutput(self, "UserPoolApiClientId",
            value=pool_api_client.user_pool_client_id,
            description="ID of the User Pool API Client")
        

        payment_request_table.grant_read_write_data(api);
        user_table.grant_read_write_data(api);
        pool.grant(api, "cognito-idp:ListUsers")
        pool.grant(api, "cognito-idp:SignUp")
        pool.grant(api, "cognito-idp:AdminGetUser")


