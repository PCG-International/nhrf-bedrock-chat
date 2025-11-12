import { BedrockChatParametersInput } from "./lib/utils/parameter-models";

export const bedrockChatParams = new Map<string, BedrockChatParametersInput>();

// v4: ECS Fargate + S3 Vectors architecture (side-by-side with v3)
bedrockChatParams.set("v4", {
  envName: "v4",
  bedrockRegion: "eu-central-1",
  allowedIpV4AddressRanges: ["0.0.0.0/1", "128.0.0.0/1"],
  allowedIpV6AddressRanges: [
    "0000:0000:0000:0000:0000:0000:0000:0000/1",
    "8000:0000:0000:0000:0000:0000:0000:0000/1",
  ],
  identityProviders: [
    {
      service: "google",
      secretName: "googleOAuthCredentials",
    },
  ],
  userPoolDomainPrefix: "nhrf-v4",
  allowedSignUpEmailDomains: [],
  autoJoinUserGroups: ["CreatingBotAllowed", "PublishAllowed"],
  selfSignUpEnabled: false,
  publishedApiAllowedIpV4AddressRanges: ["0.0.0.0/1", "128.0.0.0/1"],
  publishedApiAllowedIpV6AddressRanges: [
    "0000:0000:0000:0000:0000:0000:0000:0000/1",
    "8000:0000:0000:0000:0000:0000:0000:0000/1",
  ],
  enableRagReplicas: true,
  enableBedrockCrossRegionInference: true,
  enableLambdaSnapStart: false, // Not using Lambda for REST API
  enableBotStore: true,
  enableBotStoreReplicas: true,
  botStoreLanguage: "en",
  globalAvailableModels: [
    "claude-v4.1-opus",
    "claude-v4-sonnet",
    "claude-v3.7-sonnet",
    "amazon-nova-pro",
    "amazon-nova-lite",
    "amazon-nova-micro",
    "deepseek-r1",
  ],
  tokenValidMinutes: 30,
  alternateDomainName: "",
  hostedZoneId: "",
  devAccessIamRoleArn: "",
});
