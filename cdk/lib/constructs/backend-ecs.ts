import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import { Construct } from "constructs";

export interface BackendEcsProps {
  /**
   * VPC to deploy ECS cluster in. If not provided, a new VPC will be created.
   */
  vpc?: ec2.IVpc;

  /**
   * Environment variables to pass to the container
   */
  environment: Record<string, string>;

  /**
   * IAM role for ECS task (grants permissions to AWS services)
   */
  taskRole: iam.IRole;

  /**
   * IAM role for ECS task execution (pulls images, writes logs)
   */
  executionRole?: iam.IRole;

  /**
   * CPU units for Fargate task (256, 512, 1024, 2048, 4096)
   * Default: 1024 (1 vCPU)
   */
  cpu?: number;

  /**
   * Memory in MiB for Fargate task
   * Default: 2048 (2 GB)
   */
  memory?: number;

  /**
   * Desired number of tasks to run
   * Default: 2
   */
  desiredCount?: number;

  /**
   * Minimum number of tasks for auto scaling
   * Default: 2
   */
  minCapacity?: number;

  /**
   * Maximum number of tasks for auto scaling
   * Default: 10
   */
  maxCapacity?: number;

  /**
   * Environment name for resource tagging
   */
  envName?: string;
}

export class BackendEcs extends Construct {
  public readonly service: ecs.FargateService;
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  public readonly cluster: ecs.Cluster;
  public readonly url: string;

  constructor(scope: Construct, id: string, props: BackendEcsProps) {
    super(scope, id);

    const cpu = props.cpu || 1024;
    const memory = props.memory || 2048;
    const desiredCount = props.desiredCount || 2;
    const minCapacity = props.minCapacity || 2;
    const maxCapacity = props.maxCapacity || 10;

    // Create or use existing VPC
    const vpc =
      props.vpc ||
      new ec2.Vpc(this, "Vpc", {
        maxAzs: 2,
        natGateways: 1,
        subnetConfiguration: [
          {
            cidrMask: 24,
            name: "Public",
            subnetType: ec2.SubnetType.PUBLIC,
          },
          {
            cidrMask: 24,
            name: "Private",
            subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          },
        ],
      });

    // Create ECS Cluster
    this.cluster = new ecs.Cluster(this, "Cluster", {
      vpc,
      containerInsights: true,
      clusterName: props.envName
        ? `${props.envName}-backend-cluster`
        : undefined,
    });

    // Create execution role (for container startup)
    const executionRole =
      props.executionRole ||
      new iam.Role(this, "ExecutionRole", {
        assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName(
            "service-role/AmazonECSTaskExecutionRolePolicy"
          ),
        ],
      });

    // Create task definition
    const taskDefinition = new ecs.FargateTaskDefinition(this, "TaskDef", {
      cpu,
      memoryLimitMiB: memory,
      taskRole: props.taskRole,
      executionRole,
    });

    // Add container
    taskDefinition.addContainer("Backend", {
      image: ecs.ContainerImage.fromAsset("../backend", {
        file: "Dockerfile",
        platform: ecrAssets.Platform.LINUX_AMD64,
      }),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "backend",
        logRetention: logs.RetentionDays.THREE_MONTHS,
      }),
      environment: props.environment,
      portMappings: [
        {
          containerPort: 8000,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: [
          "CMD-SHELL",
          "curl -f http://localhost:8000/health || exit 1",
        ],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
    });

    // Create Application Load Balancer
    this.loadBalancer = new elbv2.ApplicationLoadBalancer(this, "ALB", {
      vpc,
      internetFacing: true,
      loadBalancerName: props.envName
        ? `${props.envName}-backend-alb`
        : undefined,
    });

    // Create target group
    const targetGroup = new elbv2.ApplicationTargetGroup(this, "TargetGroup", {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      healthCheck: {
        path: "/health",
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    // Create ECS Service
    this.service = new ecs.FargateService(this, "Service", {
      cluster: this.cluster,
      taskDefinition,
      desiredCount,
      assignPublicIp: false, // Deploy in private subnets
      healthCheckGracePeriod: cdk.Duration.seconds(60),
      serviceName: props.envName
        ? `${props.envName}-backend-service`
        : undefined,
    });

    // Attach service to target group
    this.service.attachToApplicationTargetGroup(targetGroup);

    // Create HTTP listener (primary) - forward to target group
    // Authentication is handled at application level via Cognito JWT tokens (same as Lambda version)
    this.loadBalancer.addListener("HttpListener", {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultAction: elbv2.ListenerAction.forward([targetGroup]),
    });

    // Auto Scaling
    const scaling = this.service.autoScaleTaskCount({
      minCapacity,
      maxCapacity,
    });

    // Scale on CPU utilization
    scaling.scaleOnCpuUtilization("CpuScaling", {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Scale on memory utilization
    scaling.scaleOnMemoryUtilization("MemoryScaling", {
      targetUtilizationPercent: 80,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    this.url = `http://${this.loadBalancer.loadBalancerDnsName}`;

    // CloudFormation Outputs
    new cdk.CfnOutput(this, "LoadBalancerDnsName", {
      value: this.loadBalancer.loadBalancerDnsName,
      description: "ALB DNS Name",
      exportName: props.envName ? `${props.envName}-BackendAlbDns` : undefined,
    });

    new cdk.CfnOutput(this, "LoadBalancerUrl", {
      value: this.url,
      description: "Backend API URL",
      exportName: props.envName ? `${props.envName}-BackendApiUrl` : undefined,
    });

    new cdk.CfnOutput(this, "ServiceName", {
      value: this.service.serviceName,
      description: "ECS Service Name",
    });

    new cdk.CfnOutput(this, "ClusterName", {
      value: this.cluster.clusterName,
      description: "ECS Cluster Name",
    });

    // Tags
    if (props.envName) {
      cdk.Tags.of(this).add("Environment", props.envName);
      cdk.Tags.of(this).add("Component", "Backend-ECS");
    }
  }
}
