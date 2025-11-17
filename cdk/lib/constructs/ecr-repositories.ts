import { Construct } from "constructs";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as cdk from "aws-cdk-lib";
import { RemovalPolicy } from "aws-cdk-lib";

export interface EcrRepositoriesProps {
  readonly envPrefix: string;
  readonly retainImages?: boolean;
}

export class EcrRepositories extends Construct {
  public readonly ecsBackendRepo: ecr.Repository;
  public readonly lambdaLightweightRepo: ecr.Repository;
  public readonly lambdaFullRepo: ecr.Repository;

  constructor(scope: Construct, id: string, props: EcrRepositoriesProps) {
    super(scope, id);

    const sepHyphen = props.envPrefix ? "-" : "";
    const retainImages = props.retainImages ?? false;

    // ECS Backend Repository (heavy image with ML dependencies)
    this.ecsBackendRepo = new ecr.Repository(this, "EcsBackendRepository", {
      repositoryName: `${props.envPrefix}${sepHyphen}bedrock-chat-backend`.toLowerCase(),
      imageScanOnPush: true,
      removalPolicy: retainImages ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      emptyOnDelete: !retainImages,
      lifecycleRules: [
        {
          description: "Remove untagged images after 7 days",
          tagStatus: ecr.TagStatus.UNTAGGED,
          maxImageAge: cdk.Duration.days(7),
          rulePriority: 1,
        },
        {
          description: "Keep last 10 images",
          maxImageCount: 10,
          rulePriority: 2,
        },
      ],
    });

    // Lambda Lightweight Repository (minimal AWS SDK dependencies)
    this.lambdaLightweightRepo = new ecr.Repository(
      this,
      "LambdaLightweightRepository",
      {
        repositoryName: `${props.envPrefix}${sepHyphen}bedrock-chat-lambda-lightweight`.toLowerCase(),
        imageScanOnPush: true,
        removalPolicy: retainImages ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
        emptyOnDelete: !retainImages,
        lifecycleRules: [
          {
            description: "Remove untagged images after 3 days",
            tagStatus: ecr.TagStatus.UNTAGGED,
            maxImageAge: cdk.Duration.days(3),
            rulePriority: 1,
          },
          {
            description: "Keep last 5 images",
            maxImageCount: 5,
            rulePriority: 2,
          },
        ],
      }
    );

    // Lambda Full Repository (Poetry dependencies for published API)
    this.lambdaFullRepo = new ecr.Repository(this, "LambdaFullRepository", {
      repositoryName: `${props.envPrefix}${sepHyphen}bedrock-chat-lambda-full`.toLowerCase(),
      imageScanOnPush: true,
      removalPolicy: retainImages ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      emptyOnDelete: !retainImages,
      lifecycleRules: [
        {
          description: "Remove untagged images after 3 days",
          tagStatus: ecr.TagStatus.UNTAGGED,
          maxImageAge: cdk.Duration.days(3),
          rulePriority: 1,
        },
        {
          description: "Keep last 5 images",
          maxImageCount: 5,
          rulePriority: 2,
        },
      ],
    });

    // Outputs for Makefile
    new cdk.CfnOutput(this, "EcsBackendRepoUri", {
      value: this.ecsBackendRepo.repositoryUri,
      description: "ECR URI for ECS Backend",
      exportName: `${props.envPrefix}${sepHyphen}EcsBackendRepoUri`,
    });

    new cdk.CfnOutput(this, "LambdaLightweightRepoUri", {
      value: this.lambdaLightweightRepo.repositoryUri,
      description: "ECR URI for Lambda Lightweight",
      exportName: `${props.envPrefix}${sepHyphen}LambdaLightweightRepoUri`,
    });

    new cdk.CfnOutput(this, "LambdaFullRepoUri", {
      value: this.lambdaFullRepo.repositoryUri,
      description: "ECR URI for Lambda Full",
      exportName: `${props.envPrefix}${sepHyphen}LambdaFullRepoUri`,
    });
  }
}