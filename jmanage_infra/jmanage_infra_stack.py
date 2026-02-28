import os
from aws_cdk import (
    # Duration,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    CfnOutput,
    aws_cognito as cognito,
    aws_s3 as s3,
    # aws_sqs as sqs,
)
from constructs import Construct

class JmanageInfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, env_name: str, **kwargs) -> None:
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

        payment_request_table.add_global_secondary_index(
            index_name="status_index",
            partition_key=dynamodb.Attribute(
                name="payment_status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_time",
                type=dynamodb.AttributeType.NUMBER
            ),
        )

        payment_request_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
        )

        user_table = dynamodb.Table(self, "User",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        calendar_table = dynamodb.Table(self, "Calendar",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        calendar_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
        )

        tour_table = dynamodb.Table(self, "Tour",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        tour_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
        )

        workspace_table = dynamodb.Table(self, "Workspace",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        workspace_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
        )

        memberships_table = dynamodb.Table(
            self, "Memberships",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),   # USER#{sub}
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),        # ACCOUNT#{account_id}
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,   # keep prod data if stack deleted
            point_in_time_recovery=True,
            time_to_live_attribute="ttl",          # optional, safe to keep
        )

        # GSI to list users by account in admin screens (optional but handy)
        memberships_table.add_global_secondary_index(
            index_name="byAccount",
            partition_key=dynamodb.Attribute(name="ACCOUNT_ID", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="USER_ID", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI to list users by workspace
        memberships_table.add_global_secondary_index(
            index_name="byWorkspace",
            partition_key=dynamodb.Attribute(name="WORKSPACE_ID", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="USER_ID", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        product_table = dynamodb.Table(
            self,
            "Product",
            partition_key=dynamodb.Attribute(
                name="pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="sk",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,     # no perder productos si borras el stack
            point_in_time_recovery=True,
        )

        order_table = dynamodb.Table(
            self,
            "Order",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        order_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
        )

        account_table = dynamodb.Table(
            self, "Account",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        file_table = dynamodb.Table(
            self, "File",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        file_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(
                name="account_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING
            ),
        )

        # ── Tournaments Feature ──────────────────────────────────────────

        tournament_table = dynamodb.Table(
            self, "Tournament",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        tournament_table.add_global_secondary_index(
            index_name="account_id_index",
            partition_key=dynamodb.Attribute(name="account_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        tournament_team_table = dynamodb.Table(
            self, "TournamentTeam",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        tournament_team_table.add_global_secondary_index(
            index_name="tournament_index",
            partition_key=dynamodb.Attribute(name="tournament_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        tournament_team_table.add_global_secondary_index(
            index_name="group_index",
            partition_key=dynamodb.Attribute(name="group_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        tournament_player_table = dynamodb.Table(
            self, "TournamentPlayer",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        tournament_player_table.add_global_secondary_index(
            index_name="tournament_index",
            partition_key=dynamodb.Attribute(name="tournament_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        tournament_player_table.add_global_secondary_index(
            index_name="team_index",
            partition_key=dynamodb.Attribute(name="team_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        tournament_match_table = dynamodb.Table(
            self, "TournamentMatch",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        tournament_match_table.add_global_secondary_index(
            index_name="tournament_index",
            partition_key=dynamodb.Attribute(name="tournament_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="date", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        tournament_match_event_table = dynamodb.Table(
            self, "TournamentMatchEvent",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        tournament_match_event_table.add_global_secondary_index(
            index_name="match_index",
            partition_key=dynamodb.Attribute(name="match_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="event_index", type=dynamodb.AttributeType.NUMBER),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI1: categoría + created_at (newest)
        product_table.add_global_secondary_index(
            index_name="GSI1_CategoryNewest",
            partition_key=dynamodb.Attribute(
                name="gsi1_pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1_sk",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI2: featured (totalSold desc usando neg_total_sold)
        product_table.add_global_secondary_index(
            index_name="GSI2_Featured",
            partition_key=dynamodb.Attribute(
                name="gsi2_pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="gsi2_sk",  # neg_total_sold
                type=dynamodb.AttributeType.NUMBER,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI3: precio (para rangos + sort asc/desc)
        product_table.add_global_secondary_index(
            index_name="GSI3_Price",
            partition_key=dynamodb.Attribute(
                name="gsi3_pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="gsi3_sk",  # price
                type=dynamodb.AttributeType.NUMBER,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI5: búsqueda por tag / token (tag search)
        product_table.add_global_secondary_index(
            index_name="GSI5_TagSearch",
            partition_key=dynamodb.Attribute(
                name="gsi5_pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="gsi5_sk",  # created_at o similar
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
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
                "account_ids": cognito.StringAttribute(mutable=True, max_len=2048),
                "accounts_roles": cognito.StringAttribute(mutable=True, max_len=2048),
            }
        )
        pool_web_client = pool.add_client("jmanage-web")
        pool_api_client = pool.add_client("jmanage-api")

        if env_name == 'prod':
            environment = {
                "SLACK_WEBHOOK_URL": os.environ.get("SLACK_WEBHOOK_URL_PROD"),
                "APP_VERSION": "prod",
                "LOG_LEVEL": "WARNING",
            }
        else:
            environment = {
                "SLACK_WEBHOOK_URL": os.environ.get("SLACK_WEBHOOK_URL_DEV"),
                "APP_VERSION": "dev",
                "LOG_LEVEL": "INFO",
            }

        api = lambda_.Function(self, "API",
            function_name=f"jmanage-api-{env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            code=lambda_.Code.from_asset("../jmanage-api/lambda_function.zip"),
            handler="app.handler",
            environment={
                "PAYMENT_REQUEST_TABLE_NAME": payment_request_table.table_name,
                "USER_TABLE_NAME": user_table.table_name,
                "CALENDAR_TABLE_NAME": calendar_table.table_name,
                "TOUR_TABLE_NAME": tour_table.table_name,
                "WORKSPACE_TABLE_NAME": workspace_table.table_name,
                "MEMBERSHIPS_TABLE_NAME": memberships_table.table_name,
                "PRODUCT_TABLE_NAME": product_table.table_name,
                "ORDER_TABLE_NAME": order_table.table_name,
                "ACCOUNT_TABLE_NAME": account_table.table_name,
                "FILE_TABLE_NAME": file_table.table_name,
                "TOURNAMENT_TABLE_NAME": tournament_table.table_name,   
                "TOURNAMENT_TEAM_TABLE_NAME": tournament_team_table.table_name,
                "TOURNAMENT_PLAYER_TABLE_NAME": tournament_player_table.table_name,
                "TOURNAMENT_MATCH_TABLE_NAME": tournament_match_table.table_name,
                "TOURNAMENT_MATCH_EVENT_TABLE_NAME": tournament_match_event_table.table_name,
                "USER_POOL_ID": pool.user_pool_id,
                "USER_POOL_API_CLIENT_ID": pool_api_client.user_pool_client_id,
                "COURIER_AUTH_TOKEN": "pk_prod_SP8ZHJC1A549JCKN1MGYF6CWDG54",
                "BUCKET_NAME": "jmanage-bucket",
                "ENV": env_name,
                **environment
            },
            timeout=Duration.seconds(120),
            memory_size=512
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
        
        CfnOutput(self, "CalendarTableName",
            value=calendar_table.table_name,
            description="Name of the Calendar table")
        
        CfnOutput(self, "TourTableName",
            value=tour_table.table_name,
            description="Name of the Tour table")
        
        CfnOutput(self, "WorkspaceTableName",
            value=workspace_table.table_name,
            description="Name of the Workspace table")   
        
        CfnOutput(self, "MembershipsTableName",
            value=memberships_table.table_name,
            description="Name of the Memberships table")
        
        CfnOutput(self, "ProductTableName",
            value=product_table.table_name,
            description="Name of the Product table")

        CfnOutput(self, "OrderTableName",
            value=order_table.table_name,
            description="Name of the Order table")
        
        CfnOutput(self, "AccountTableName",
            value=account_table.table_name,
            description="Name of the Account table")
        
        CfnOutput(self, "FileTableName",
            value=file_table.table_name,
            description="Name of the File table")
        
        CfnOutput(self, "TournamentTableName",
            value=tournament_table.table_name,
            description="Name of the Tournament table")
        
        CfnOutput(self, "TournamentTeamTableName",
            value=tournament_team_table.table_name,
            description="Name of the TournamentTeam table")
        
        CfnOutput(self, "TournamentPlayerTableName",
            value=tournament_player_table.table_name,
            description="Name of the TournamentPlayer table")
        
        CfnOutput(self, "TournamentMatchTableName",
            value=tournament_match_table.table_name,
            description="Name of the TournamentMatch table")
        
        CfnOutput(self, "TournamentMatchEventTableName",
            value=tournament_match_event_table.table_name,
            description="Name of the TournamentMatchEvent table")
        
        
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
        calendar_table.grant_read_write_data(api);
        tour_table.grant_read_write_data(api);
        workspace_table.grant_read_write_data(api);
        memberships_table.grant_read_write_data(api);
        product_table.grant_read_write_data(api);
        order_table.grant_read_write_data(api);
        account_table.grant_read_write_data(api);
        file_table.grant_read_write_data(api);
        tournament_table.grant_read_write_data(api);
        tournament_team_table.grant_read_write_data(api);
        tournament_player_table.grant_read_write_data(api);
        tournament_match_table.grant_read_write_data(api);
        tournament_match_event_table.grant_read_write_data(api);
        pool.grant(api, "cognito-idp:ListUsers")
        pool.grant(api, "cognito-idp:SignUp")
        pool.grant(api, "cognito-idp:AdminGetUser")
        pool.grant(api, "cognito-idp:AdminUpdateUserAttributes")
        pool.grant(api, "cognito-idp:AdminDeleteUser")
        pool.grant(api, "cognito-idp:AdminDisableUser")
        pool.grant(api, "cognito-idp:AdminEnableUser")
