import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as aws_ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3_assets from 'aws-cdk-lib/aws-s3-assets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secrets from 'aws-cdk-lib/aws-secretsmanager';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';
import * as path from 'path';
import * as dotenv from 'dotenv';
dotenv.config({
  path: path.resolve(__dirname, "../../../.env")
});

export class InfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    //===============DATA BUCKET===============
    // Where transcripts go. Also used to transfer the script to EC2.
    const bucket = new s3.Bucket(this, 'TranscriptBucket', {
      bucketName: 'publicpolitic',
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For testing only
      autoDeleteObjects: true,
    });

    //===============DYNAMODB===============
    const table = new dynamodb.TableV2(this, 'MeetingsTable', {
      tableName: 'CouncilMeetings',
      partitionKey: {name: 'video_id', type: dynamodb.AttributeType.STRING},
      removalPolicy: cdk.RemovalPolicy.DESTROY
    })

    //===============SECRETS MANAGER===============
    // Storing Proxy URL username and password
    const proxy_secrets = new secrets.Secret(this, 'ProxySecrets', {
      secretName: 'publicpolitic/proxy_secrets',
      description: 'The username and password for the Proxy URL',
      secretObjectValue: {
        PROXY_USER: cdk.SecretValue.unsafePlainText(process.env.PROXY_USER || ''),
        PROXY_PASS_BASE: cdk.SecretValue.unsafePlainText(process.env.PROXY_PASS_BASE || '')
      }
    })

    //===============NETWORKING (Simple & Cheap)===============
    // We create a VPC with ONLY Public subnets. 
    // This allows the EC2 to talk to YouTube/AWS APIs without an expensive NAT Gateway.
    const vpc = new ec2.Vpc(this, 'SimpleVPC', {
      maxAzs: 1,
      natGateways: 0,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: 'PublicSubnet',
          subnetType: ec2.SubnetType.PUBLIC,
        },
      ],
    });

    //===============EC2 ASSET ( The Script )===============
    // This takes your local 'main.py' and zips it to S3 so EC2 can download it.
    const scriptAsset = new s3_assets.Asset(this, 'SoldierScriptAsset', {
      path: path.join(__dirname, '../../files/ec2_soldier_code.py'),
    });

    //===============EC2 INSTANCE ( The Soldier )===============
    const soldierRole = new iam.Role(this, 'SoldierRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
    });

    // Grant permissions to the Soldier
    bucket.grantReadWrite(soldierRole); // Write transcripts
    scriptAsset.grantRead(soldierRole); // Download its own code
    proxy_secrets.grantRead(soldierRole); // Read the username and password 
    soldierRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')); // For debugging via Console
    soldierRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonTranscribeFullAccess'));
    
    // Allow EC2 to read the SSM Parameter created by Lambda
    soldierRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:PutParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/meeting/*`],
    }));

    const soldier = new ec2.Instance(this, 'SoldierInstance', {
      instanceName: 'Soldier',
      vpc: vpc,
      role: soldierRole,
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
      // Use Ubuntu 24.04 (Noble)
      machineImage: ec2.MachineImage.lookup({
        name: 'ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*',
        owners: ['099720109477'], // Canonical (Official Ubuntu Owner ID)
      }),
      // USER DATA: The script that runs when the instance boots
      userData: ec2.UserData.forLinux(),
      userDataCausesReplacement: true
    });

    // Replace your current userData.addCommands with this:

    const userDataScript = `#!/bin/bash
set -e

# --- A. Install Dependencies ---
echo "Installing OS dependencies..."
apt-get update
apt-get install -y python3-pip ffmpeg unzip

# --- B. Install AWS CLI v2 (via curl as requested) ---
echo "Installing AWS CLI v2..."
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install
rm -rf aws awscliv2.zip

# --- C. Install Python Libraries ---
echo "Installing Python libraries..."
# --break-system-packages is needed on newer Ubuntu versions (24.04+)
pip3 install boto3 faster-whisper numpy yt-dlp --break-system-packages

# --- D. Download the Application Code ---
echo "Downloading soldier script from S3..."
# We use the S3 URL provided by the CDK asset
aws s3 cp ${scriptAsset.s3ObjectUrl} /home/ubuntu/recorder.py
chown ubuntu:ubuntu /home/ubuntu/recorder.py
chmod 700 /home/ubuntu/recorder.py

# --- E. Setup Systemd Service ---
echo "Configuring Systemd service..."
cat <<EOF > /etc/systemd/system/council-recorder.service
[Unit]
Description=YouTube Council Meeting Recorder
# Start only after network is fully up
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
# Point to the downloaded file
ExecStart=/usr/bin/python3 -u /home/ubuntu/recorder.py
# Restart if it crashes (optional, good for resilience)
Restart=on-failure
RestartSec=10
# Logging
StandardOutput=journal+console
StandardError=journal+console
# Environment Variables
Environment="BUCKET_NAME=${bucket.bucketName}"
Environment="AWS_DEFAULT_REGION=${this.region}"
Environment="SSM_VIDEO_ID_PARAM=/meeting/current_video_id"

[Install]
WantedBy=multi-user.target
EOF

# --- F. Enable the Service (Do NOT start it yet) ---
echo "Enabling service to run on future boots..."
systemctl daemon-reload
systemctl enable council-recorder.service

# --- G. Auto-Shutdown After Deployment ---
echo "Initial deployment and setup complete."
echo "Shutting down instance to save costs until the StepFunction wakes it up..."
shutdown -h now
    `;

    // 4. Attach the User Data to the Instance
    soldier.addUserData(userDataScript);
    table.grantReadWriteData(soldier)

    //===============LAMBDA FUNCTION ( The Historian )===============
    const historian_lambda = new lambda.DockerImageFunction(this, 'HistorianLambda', {
      functionName: 'historian-lambda',
      description: 'Lambda that routinely summarizes transcript of live Youtube meeting',
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../../services/lambdas/historian_lambda'),
        {
          platform: aws_ecr_assets.Platform.LINUX_AMD64
        }
      ),
      timeout: cdk.Duration.minutes(15),
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: bucket.bucketName
      }
    })

    bucket.grantRead(historian_lambda)
    table.grantReadWriteData(historian_lambda)
    historian_lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: ["*"]
    }))
    historian_lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:PutParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/meeting/*`]
    }))

    //===============STEP FUNCTION ORCHESTRATOR===============

    // Task 1: Start EC2 instance when meeting starts
    const task_1_start_ec2 = new tasks.CallAwsService(this, 'StartEC2', {
      service: 'ec2',
      action: 'startInstances',
      parameters: {'InstanceIds': [soldier.instanceId]},
      iamResources: [`arn:aws:ec2:${this.region}:${this.account}:instance/${soldier.instanceId}`],
      resultPath: sfn.JsonPath.DISCARD
    })

    // Task 2: Wait N minutes before summarization
    const task_2_wait = new sfn.Wait(this, 'Wait', {
      time: sfn.WaitTime.duration(cdk.Duration.minutes(4))
    })

    // Task 3: Trigger summarization lambda
    const task_3_historian = new tasks.LambdaInvoke(this, "InvokeHistorian", {
      lambdaFunction: historian_lambda,
      payloadResponseOnly: true
    })

    // Task 4: Stop EC2 instance once meeting is done
    const task_4_stop_ec2 = new tasks.CallAwsService(this, 'StopEC2', {
      service: 'ec2',
      action: 'stopInstances',
      parameters: {'InstanceIds': [soldier.instanceId]},
      iamResources: [`arn:aws:ec2:${this.region}:${this.account}:instance/${soldier.instanceId}`],
    })

    // Logic: Meeting status
    const check_meeting_active = new sfn.Choice(this, 'IsMeetingActive')

    // Chain workflow together
    task_1_start_ec2.next(task_2_wait)
    task_2_wait.next(task_3_historian)
    task_3_historian.next(check_meeting_active)

    // If meeting active, loop over, else terminate
    check_meeting_active.when(sfn.Condition.booleanEquals('$.meeting_active', true), task_2_wait)
    check_meeting_active.otherwise(task_4_stop_ec2)

    // Create State Machine
    const state_machine = new sfn.StateMachine(this, 'MeetingOrchestrator', {
      stateMachineName: 'meeting-orchestrator',
      definitionBody: sfn.DefinitionBody.fromChainable(task_1_start_ec2),
      timeout: cdk.Duration.hours(1)
    })

    //===============LAMBDA FUNCTION ( The Scout )===============
    const scout = new lambda.DockerImageFunction(this, 'ScoutFunction', {
      functionName: 'scout-lambda',
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../../services/lambdas/scout_lambda'),
      {
        platform: aws_ecr_assets.Platform.LINUX_AMD64
      }),
      timeout: cdk.Duration.minutes(15),
      environment: {
        YOUTUBE_API_KEY: process.env.YOUTUBE_API_KEY || '', // Ideally use Secrets Manager
        CHANNEL_ID: process.env.CHANNEL_ID || '',
        STATE_MACHINE_ARN: state_machine.stateMachineArn,
        TABLE_NAME: table.tableName
      },
    });

    //===============LAMBDA FUNCTION ( The Chatbot )===============
    const chatbot_lambda = new lambda.DockerImageFunction(this, 'ChatbotFunction', {
      functionName: 'chatbot-lambda',
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../../services/lambdas/chatbot_lambda'),
      {
        platform: aws_ecr_assets.Platform.LINUX_AMD64
      }),
      timeout: cdk.Duration.minutes(15),
      environment: {
        BUCKET_NAME: bucket.bucketName,
        KNOWLEDGE_BASE_ID: process.env.KNOWLEDGE_BASE_ID || ""
      },
    });

    // Grant Permissions to Lambda
    scout.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:PutParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/meeting/*`],
    }));
    table.grantReadWriteData(scout)
    state_machine.grantStartExecution(scout)

    //===============SCHEDULER===============
    // Run every 15 minutes
    new events.Rule(this, 'ScoutSchedule', {
      schedule: events.Schedule.rate(cdk.Duration.hours(1)),
      targets: [new targets.LambdaFunction(scout)],
    });

    // new events.Rule(this, 'ScoutSchedule', {
    //   schedule: events.Schedule.cron({
    //     minute: '0/5',           // Every 5 minutes
    //     hour: '19',              // At 19:00 hours (7:00 PM)
    //     month: '*',              // Every month
    //     weekDay: 'TUE#1,TUE#3',  // The 1st and 3rd Tuesday
    //     year: '*'                // Every year
    //   }),
    //   targets: [new targets.LambdaFunction(scout)],
    // });

    // Outputs
    new cdk.CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'InstanceId', { value: soldier.instanceId });
    new cdk.CfnOutput(this, 'ProxyUser', {value: process.env.PROXY_USER || ""})
    new cdk.CfnOutput(this, 'ProxyPassBase', {value: process.env.PROXY_PASS_BASE || ""})
    
  }
}
