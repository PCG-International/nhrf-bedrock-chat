import { CfnOutput, RemovalPolicy, StackProps, IgnoreMode } from "aws-cdk-lib";
import {
  BlockPublicAccess,
  Bucket,
  BucketEncryption,
  ObjectOwnership,
} from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { Bucket as VectorBucket } from "cdk-s3-vectors";
import { Auth } from "./constructs/auth";
import { Api } from "./constructs/api";
import { BackendEcs } from "./constructs/backend-ecs";
import { Database } from "./constructs/database";
import { Frontend } from "./constructs/frontend";
import { WebSocket } from "./constructs/websocket";
import * as cdk from "aws-cdk-lib";
import { Embedding } from "./constructs/embedding";
import { UsageAnalysis } from "./constructs/usage-analysis";
import { TIdentityProvider, identityProvider } from "./utils/identity-provider";
import { ApiPublishCodebuild } from "./constructs/api-publish-codebuild";
import { WebAclForPublishedApi } from "./constructs/webacl-for-published-api";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as path from "path";
import { BedrockCustomBotCodebuild } from "./constructs/bedrock-custom-bot-codebuild";
import { BotStore, Language } from "./constructs/bot-store";
import { EcrRepositories } from "./constructs/ecr-repositories";
import { Duration } from "aws-cdk-lib";

export interface BedrockChatStackProps extends StackProps {
  readonly envName: string;
  readonly envPrefix: string;
  readonly bedrockRegion: string;
  readonly webAclId: string;
  readonly identityProviders: TIdentityProvider[];
  readonly userPoolDomainPrefix: string;
  readonly publishedApiAllowedIpV4AddressRanges: string[];
  readonly publishedApiAllowedIpV6AddressRanges: string[];
  readonly allowedSignUpEmailDomains: string[];
  readonly autoJoinUserGroups: string[];
  readonly selfSignUpEnabled: boolean;
  readonly enableIpV6: boolean;
  readonly documentBucket: Bucket;
  readonly enableRagReplicas: boolean;
  readonly enableBedrockCrossRegionInference: boolean;
  readonly enableLambdaSnapStart: boolean;
  readonly enableBotStore: boolean;
  readonly enableBotStoreReplicas: boolean;
  readonly botStoreLanguage: Language;
  readonly globalAvailableModels?: string[];
  readonly tokenValidMinutes: number;
  readonly alternateDomainName?: string;
  readonly hostedZoneId?: string;
  readonly devAccessIamRoleArn?: string;
}

export class BedrockChatStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BedrockChatStackProps) {
    super(scope, id, {
      description: "Bedrock Chat Stack (uksb-1tupboc46)",
      ...props,
    });

    const sepHyphen = props.envPrefix ? "-" : "";
    const idp = identityProvider(props.identityProviders);

    // Create ECR repositories for Docker images (only for v4 ECS deployment)
    let ecrRepos: EcrRepositories | undefined;
    let ecrReposForLambda: EcrRepositories | undefined;
    // Use ECR for ECS backend (pushed by GitHub Actions), but build Lambda images from source
    const useEcrForEcs = true;    // GitHub Actions pushes ECS backend image
    const useEcrForLambda = false; // Lambda images still built from source

    if (props.envName === "v4") {
      ecrRepos = new EcrRepositories(this, "EcrRepositories", {
        envPrefix: props.envPrefix,
        retainImages: false, // Set to true for production
      });

      ecrReposForLambda = useEcrForLambda ? ecrRepos : undefined;
    }

    const accessLogBucket = new Bucket(this, "AccessLogBucket", {
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: ObjectOwnership.OBJECT_WRITER,
      autoDeleteObjects: true,
    });

    // Bucket for source code
    const sourceBucket = new Bucket(this, "SourceBucketForCodeBuild", {
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: ObjectOwnership.OBJECT_WRITER,
      autoDeleteObjects: true,
      serverAccessLogsBucket: accessLogBucket,
      serverAccessLogsPrefix: "SourceBucketForCodeBuild",
    });
    new s3deploy.BucketDeployment(this, "SourceDeploy", {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, "../../"), {
          ignoreMode: IgnoreMode.GIT,
          exclude: [
            "**/node_modules/**",
            "**/dist/**",
            "**/dev-dist/**",
            "**/.venv/**",
            "**/__pycache__/**",
            "**/cdk.out/**",
            "**/.vscode/**",
            "**/.DS_Store/**",
            "**/.git/**",
            "**/.github/**",
            "**/.mypy_cache/**",
            "**/examples/**",
            "**/docs/**",
            "**/.env",
            "**/.env.local",
            "**/.gitignore",
            "**/test/**",
            "**/tests/**",
            "**/backend/embedding_statemachine/pdf_ai_ocr/**",
            "**/backend/guardrails/**",
          ],
        }),
      ],
      destinationBucket: sourceBucket,
      logRetention: logs.RetentionDays.THREE_MONTHS,
      memoryLimit: 2048, // Increase memory to 2GB for large asset deployment
    });

    // Shared S3 Vector Bucket for all bot Knowledge Bases (replaces per-bot OpenSearch collections)
    // This single bucket can host thousands of vector indexes at minimal cost (~$0.06/bot/month)
    const sharedVectorBucket = new VectorBucket(this, "SharedVectorBucket", {
      vectorBucketName:
        `${props.envPrefix}${sepHyphen}bedrock-vectors`.toLowerCase(),
    });

    // Export ARN and name so bot stacks can reference it
    new CfnOutput(this, "SharedVectorBucketArn", {
      value: sharedVectorBucket.vectorBucketArn,
      description: "Shared S3 Vector Bucket ARN for bot Knowledge Bases",
      exportName: `${props.envName}-SharedVectorBucketArn`,
    });

    new CfnOutput(this, "SharedVectorBucketName", {
      value: sharedVectorBucket.vectorBucketName,
      description: "Shared S3 Vector Bucket Name",
      exportName: `${props.envName}-SharedVectorBucketName`,
    });

    // CodeBuild used for api publication
    const apiPublishCodebuild = new ApiPublishCodebuild(
      this,
      "ApiPublishCodebuild",
      {
        sourceBucket,
        envName: props.envName,
        envPrefix: props.envPrefix,
        bedrockRegion: props.bedrockRegion,
      }
    );
    // CodeBuild used for KnowledgeBase
    const bedrockCustomBotCodebuild = new BedrockCustomBotCodebuild(
      this,
      "BedrockKnowledgeBaseCodebuild",
      {
        sourceBucket,
        envName: props.envName,
        envPrefix: props.envPrefix,
        bedrockRegion: props.bedrockRegion,
      }
    );

    // Determine the frontend origin early (needed for Auth)
    // For alternate domain, we know the URL without creating CloudFront yet
    const frontendOrigin = props.alternateDomainName
      ? `https://${props.alternateDomainName.replace(/\/$/, "")}` // Remove trailing slash
      : ""; // Will be set after frontend creation for default domain

    const auth = new Auth(this, "Auth", {
      origin: frontendOrigin || "https://placeholder", // Temporary, will be updated
      userPoolDomainPrefixKey: props.userPoolDomainPrefix,
      idp,
      allowedSignUpEmailDomains: props.allowedSignUpEmailDomains,
      autoJoinUserGroups: props.autoJoinUserGroups,
      selfSignUpEnabled: props.selfSignUpEnabled,
      tokenValidity: Duration.minutes(props.tokenValidMinutes),
    });
    const largeMessageBucket = new Bucket(this, "LargeMessageBucket", {
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: ObjectOwnership.OBJECT_WRITER,
      autoDeleteObjects: true,
      serverAccessLogsBucket: accessLogBucket,
      serverAccessLogsPrefix: "LargeMessageBucket",
    });

    const database = new Database(this, "Database", {
      // Enable PITR to export data to s3
      pointInTimeRecovery: true,
    });

    // Custom Bot Store
    let botStore = undefined;
    if (props.enableBotStore) {
      botStore = new BotStore(this, "BotStore", {
        envPrefix: props.envPrefix,
        botTable: database.botTable,
        conversationTable: database.conversationTable,
        language: props.botStoreLanguage,
        enableBotStoreReplicas: props.enableBotStoreReplicas,
      });
    }

    const usageAnalysis = new UsageAnalysis(this, "UsageAnalysis", {
      envPrefix: props.envPrefix,
      accessLogBucket,
      sourceDatabase: database,
    });

    // Determine whether to use ECS or Lambda based on environment
    const useEcs = props.envName === "v4";

    let backendApi: Api | undefined;
    let backendEcs: BackendEcs | undefined;
    let backendEndpoint: string;
    let backendTaskRole: iam.IRole | undefined;
    let frontend: Frontend;

    if (useEcs) {
      // For v4 (ECS): Create backend first, then frontend with ALB origin
      // v4: Use ECS Fargate architecture
      // Create task role with all necessary permissions
      const taskRole = new iam.Role(this, "EcsTaskRole", {
        assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      });

      // Grant DynamoDB access
      database.conversationTable.grantReadWriteData(taskRole);
      database.botTable.grantReadWriteData(taskRole);
      database.websocketSessionTable.grantReadWriteData(taskRole);

      // Grant Bedrock permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["bedrock:*"],
          resources: ["*"],
        })
      );

      // Grant CodeBuild permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
          resources: [
            apiPublishCodebuild.project.projectArn,
            bedrockCustomBotCodebuild.project.projectArn,
          ],
        })
      );

      // Grant CloudFormation permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "cloudformation:DescribeStacks",
            "cloudformation:DeleteStack",
          ],
          resources: ["*"],
        })
      );

      // Grant API Gateway permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["apigateway:*"],
          resources: ["*"],
        })
      );

      // Grant Athena and Glue permissions for usage analysis
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "athena:StartQueryExecution",
            "athena:GetQueryExecution",
            "athena:GetQueryResults",
          ],
          resources: [usageAnalysis.workgroupArn],
        })
      );
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["glue:GetDatabase", "glue:GetTable"],
          resources: [
            `arn:aws:glue:${this.region}:${this.account}:catalog`,
            `arn:aws:glue:${this.region}:${this.account}:database/${usageAnalysis.database.databaseName}`,
            `arn:aws:glue:${this.region}:${this.account}:table/${usageAnalysis.database.databaseName}/*`,
          ],
        })
      );

      // Grant Cognito permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "cognito-idp:AdminListGroupsForUser",
            "cognito-idp:AdminGetUser",
          ],
          resources: [auth.userPool.userPoolArn],
        })
      );

      // Grant permission to assume TableAccessRole (for row-level security)
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["sts:AssumeRole"],
          resources: [database.tableAccessRole.roleArn],
        })
      );

      // Grant Secrets Manager permissions
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "secretsmanager:GetSecretValue",
            "secretsmanager:DescribeSecret",
            "secretsmanager:CreateSecret",
            "secretsmanager:UpdateSecret",
          ],
          resources: ["*"],
        })
      );

      // Grant S3 permissions
      props.documentBucket.grantReadWrite(taskRole);
      largeMessageBucket.grantReadWrite(taskRole);
      usageAnalysis.ddbBucket.grantReadWrite(taskRole);
      usageAnalysis.resultOutputBucket.grantReadWrite(taskRole);

      // Create ECS backend
      backendEcs = new BackendEcs(this, "BackendEcs", {
        taskRole,
        account: this.account,
        region: this.region,
        ecrRepos: useEcrForEcs ? ecrRepos : undefined, // Use ECR image pushed by GitHub Actions
        environment: {
          ACCOUNT: this.account,
          REGION: this.region,
          BEDROCK_REGION: props.bedrockRegion,
          CONVERSATION_TABLE_NAME: database.conversationTable.tableName,
          BOT_TABLE_NAME: database.botTable.tableName,
          DOCUMENT_BUCKET: props.documentBucket.bucketName,
          DOCUMENT_BUCKET_NAME: props.documentBucket.bucketName,
          LARGE_MESSAGE_BUCKET_NAME: largeMessageBucket.bucketName,
          USER_POOL_ID: auth.userPool.userPoolId,
          CLIENT_ID: auth.client.userPoolClientId,
          TABLE_ACCESS_ROLE_ARN: database.tableAccessRole.roleArn,
          PUBLISH_API_CODEBUILD_PROJECT_NAME:
            apiPublishCodebuild.project.projectName,
          ENV_NAME: props.envName,
          ENV_PREFIX: props.envPrefix,
          CORS_ALLOW_ORIGINS: frontendOrigin || "*", // Use pre-computed origin
          ENABLE_BEDROCK_CROSS_REGION_INFERENCE:
            props.enableBedrockCrossRegionInference.toString(),
          GLOBAL_AVAILABLE_MODELS: props.globalAvailableModels?.join(",") || "",
          OPENSEARCH_DOMAIN_ENDPOINT: botStore?.openSearchEndpoint || "",
          USAGE_ANALYSIS_DATABASE: usageAnalysis.database.databaseName,
          USAGE_ANALYSIS_TABLE: usageAnalysis.ddbExportTable.tableName,
          USAGE_ANALYSIS_WORKGROUP: usageAnalysis.workgroupName,
          USAGE_ANALYSIS_OUTPUT_LOCATION: `s3://${usageAnalysis.resultOutputBucket.bucketName}`,
        },
        envName: props.envName,
        // Single task configuration (no autoscaling)
        desiredCount: 1,
        minCapacity: 1,
        maxCapacity: 1,
      });

      // Now create frontend with the ALB as an origin
      frontend = new Frontend(this, "Frontend", {
        accessLogBucket,
        webAclId: props.webAclId,
        enableIpV6: props.enableIpV6,
        alternateDomainName: props.alternateDomainName,
        hostedZoneId: props.hostedZoneId,
        backendAlb: backendEcs.loadBalancer, // Pass ALB to route /api/* through CloudFront
      });

      // Use CloudFront URL for backend API (HTTPS)
      backendEndpoint = frontend.getBackendApiEndpoint();
      backendTaskRole = taskRole;

      // Add permissions for BotStore
      if (backendTaskRole && botStore) {
        botStore.addDataAccessPolicy(
          props.envPrefix,
          "DAPolicyEcsTask",
          backendTaskRole,
          ["aoss:DescribeCollectionItems"],
          ["aoss:DescribeIndex", "aoss:ReadDocument"]
        );
      }
    } else {
      // Default: Use Lambda architecture
      // For v3: Create frontend first (no backend ALB), then backend
      frontend = new Frontend(this, "Frontend", {
        accessLogBucket,
        webAclId: props.webAclId,
        enableIpV6: props.enableIpV6,
        alternateDomainName: props.alternateDomainName,
        hostedZoneId: props.hostedZoneId,
        // No backendAlb for Lambda architecture
      });

      backendApi = new Api(this, "BackendApi", {
        envName: props.envName,
        envPrefix: props.envPrefix,
        database,
        auth,
        bedrockRegion: props.bedrockRegion,
        documentBucket: props.documentBucket,
        apiPublishProject: apiPublishCodebuild.project,
        bedrockCustomBotProject: bedrockCustomBotCodebuild.project,
        usageAnalysis,
        largeMessageBucket,
        enableBedrockCrossRegionInference:
          props.enableBedrockCrossRegionInference,
        enableLambdaSnapStart: props.enableLambdaSnapStart,
        openSearchEndpoint: botStore?.openSearchEndpoint,
        globalAvailableModels: props.globalAvailableModels,
      });

      backendEndpoint = backendApi.api.apiEndpoint;
      props.documentBucket.grantReadWrite(backendApi.handler);

      // Add permissions to API handler for BotStore
      if (backendApi.handler.role && botStore) {
        botStore.addDataAccessPolicy(
          props.envPrefix,
          "DAPolicyApiHandler",
          backendApi.handler.role,
          ["aoss:DescribeCollectionItems"],
          ["aoss:DescribeIndex", "aoss:ReadDocument"]
        );
      }
    }

    // Add data access policy for developers
    // Get IAM user/role ARN from environment variables
    if (props.devAccessIamRoleArn) {
      // Access to BotStore
      botStore?.addDataAccessPolicy(
        props.envPrefix,
        "DAPolicyDevAccess",
        iam.Role.fromRoleArn(
          this,
          "DevAccessIamRoleArn",
          props.devAccessIamRoleArn
        ),
        [
          "aoss:DescribeCollectionItems",
          "aoss:CreateCollectionItems",
          "aoss:DeleteCollectionItems",
          "aoss:UpdateCollectionItems",
        ],
        [
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
        ]
      );
    }

    // WebSocket for streaming responses
    // V3: Uses API Gateway WebSocket + Lambda (proven, stable)
    // V4: Uses CloudFront → ALB → ECS WebSocket (unified architecture)
    let webSocketApiEndpoint: string;

    if (useEcs) {
      // V4: WebSocket through CloudFront and ECS
      // Convert https:// to wss:// for WebSocket protocol
      const httpOrigin = frontend.getOrigin();
      const wsOrigin = httpOrigin
        .replace("https://", "wss://")
        .replace("http://", "ws://");
      webSocketApiEndpoint = `${wsOrigin}/api/ws`;
    } else {
      // V3: WebSocket through API Gateway + Lambda
      const websocket = new WebSocket(this, "WebSocket", {
        accessLogBucket,
        database,
        websocketSessionTable: database.websocketSessionTable,
        auth,
        bedrockRegion: props.bedrockRegion,
        largeMessageBucket,
        documentBucket: props.documentBucket,
        enableBedrockCrossRegionInference:
          props.enableBedrockCrossRegionInference,
        enableLambdaSnapStart: props.enableLambdaSnapStart,
      });
      webSocketApiEndpoint = websocket.apiEndpoint;
    }

    frontend.buildViteApp({
      backendApiEndpoint: backendEndpoint,
      webSocketApiEndpoint: webSocketApiEndpoint,
      userPoolDomainPrefix: props.userPoolDomainPrefix,
      auth,
      idp,
    });

    const embedding = new Embedding(this, "Embedding", {
      bedrockRegion: props.bedrockRegion,
      database,
      documentBucket: props.documentBucket,
      bedrockCustomBotProject: bedrockCustomBotCodebuild.project,
      enableRagReplicas: props.enableRagReplicas,
      ecrRepos: ecrReposForLambda, // Pass ECR repositories (only when images exist)
    });

    // WebAcl for published API
    const webAclForPublishedApi = new WebAclForPublishedApi(
      this,
      "WebAclForPublishedApi",
      {
        envPrefix: props.envPrefix,
        allowedIpV4AddressRanges: props.publishedApiAllowedIpV4AddressRanges,
        allowedIpV6AddressRanges: props.publishedApiAllowedIpV6AddressRanges,
      }
    );

    new CfnOutput(this, "DocumentBucketName", {
      value: props.documentBucket.bucketName,
    });
    new CfnOutput(this, "FrontendURL", {
      value: frontend.getOrigin(),
    });

    // Outputs for API publication
    new CfnOutput(this, "PublishedApiWebAclArn", {
      value: webAclForPublishedApi.webAclArn,
      exportName: `${props.envPrefix}${sepHyphen}PublishedApiWebAclArn`,
    });
    new CfnOutput(this, "ConversationTableNameV3", {
      value: database.conversationTable.tableName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatConversationTableName`,
    });
    new CfnOutput(this, "BotTableNameV3", {
      value: database.botTable.tableName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatBotTableNameV3`,
    });
    new CfnOutput(this, "TableAccessRoleArn", {
      value: database.tableAccessRole.roleArn,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatTableAccessRoleArn`,
    });
    new CfnOutput(this, "LargeMessageBucketName", {
      value: largeMessageBucket.bucketName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatLargeMessageBucketName`,
    });
  }
}
