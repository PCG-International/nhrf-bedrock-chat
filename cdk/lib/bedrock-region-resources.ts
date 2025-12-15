import {
  CfnOutput,
  RemovalPolicy,
  Stack,
  StackProps,
  CustomResource,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import {
  BlockPublicAccess,
  Bucket,
  BucketEncryption,
  ObjectOwnership,
  HttpMethods,
} from "aws-cdk-lib/aws-s3";
import * as cr from "aws-cdk-lib/custom-resources";
import * as iam from "aws-cdk-lib/aws-iam";

interface BedrockRegionResourcesStackProps extends StackProps {}

export class BedrockRegionResourcesStack extends Stack {
  readonly documentBucket: Bucket;

  constructor(
    scope: Construct,
    id: string,
    props: BedrockRegionResourcesStackProps
  ) {
    super(scope, id, props);

    const prefix = Stack.of(this).region;

    const accessLogBucket = new Bucket(this, `${prefix}AccessLogBucket`, {
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: ObjectOwnership.OBJECT_WRITER,
      autoDeleteObjects: true,
    });

    this.documentBucket = new Bucket(this, `${prefix}DocumentBucket`, {
      encryption: BucketEncryption.S3_MANAGED,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: ObjectOwnership.OBJECT_WRITER,
      autoDeleteObjects: true,
      serverAccessLogsBucket: accessLogBucket,
      serverAccessLogsPrefix: "DocumentBucket",
      cors: [
        {
          allowedMethods: [
            HttpMethods.PUT,
            HttpMethods.POST,
            HttpMethods.GET,
            HttpMethods.HEAD,
          ],
          allowedOrigins: ["*"],
          allowedHeaders: ["*"],
          exposedHeaders: ["ETag"],
          maxAge: 3000,
        },
      ],
    });

    // Custom Resource to ensure CORS is applied to the bucket
    // This is necessary because CDK sometimes doesn't update CORS on existing buckets
    const corsUpdater = new cr.AwsCustomResource(
      this,
      "DocumentBucketCorsUpdater",
      {
        onUpdate: {
          service: "S3",
          action: "putBucketCors",
          parameters: {
            Bucket: this.documentBucket.bucketName,
            CORSConfiguration: {
              CORSRules: [
                {
                  AllowedHeaders: ["*"],
                  AllowedMethods: ["PUT", "POST", "GET", "HEAD"],
                  AllowedOrigins: ["*"],
                  ExposedHeaders: ["ETag"],
                  MaxAgeSeconds: 3000,
                },
              ],
            },
          },
          physicalResourceId: cr.PhysicalResourceId.of(
            `DocumentBucketCors-${this.documentBucket.bucketName}`
          ),
        },
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ["s3:PutBucketCors", "s3:GetBucketCors"],
            resources: [this.documentBucket.bucketArn],
          }),
        ]),
      }
    );

    // Ensure CORS is applied after bucket is created
    corsUpdater.node.addDependency(this.documentBucket);

    new CfnOutput(this, "DocumentBucketName", {
      value: this.documentBucket.bucketName,
    });
  }
}
