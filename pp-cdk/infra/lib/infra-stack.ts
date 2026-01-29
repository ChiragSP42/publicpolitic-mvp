import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as aws_ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3_assets from 'aws-cdk-lib/aws-s3-assets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import * as path from 'path';
import * as dotenv from 'dotenv';
dotenv.config();

export class InfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // 1. DATA BUCKET
    // Where transcripts go. Also used to transfer the script to EC2.
    const bucket = new s3.Bucket(this, 'TranscriptBucket', {
      bucketName: 'publicpolitic',
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For testing only
      autoDeleteObjects: true,
    });

    // 2. NETWORKING (Simple & Cheap)
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

    // 3. EC2 ASSET ( The Script )
    // This takes your local 'main.py' and zips it to S3 so EC2 can download it.
    const scriptAsset = new s3_assets.Asset(this, 'SoldierScriptAsset', {
      path: path.join(__dirname, '../../files/ec2_soldier_code.py'),
    });

    // 4. EC2 INSTANCE ( The Soldier )
    const soldierRole = new iam.Role(this, 'SoldierRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
    });

    // Grant permissions to the Soldier
    bucket.grantReadWrite(soldierRole); // Write transcripts
    scriptAsset.grantRead(soldierRole); // Download its own code
    soldierRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')); // For debugging via Console
    soldierRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonTranscribeFullAccess'));
    
    // Allow EC2 to read the SSM Parameter created by Lambda
    soldierRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
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

# --- F. Enable and Start the Service ---
echo "Starting service..."
systemctl daemon-reload
systemctl enable council-recorder.service
systemctl start council-recorder.service
    `;

    // 4. Attach the User Data to the Instance
    soldier.addUserData(userDataScript);


    // Add startup commands to User Data
    // soldier.userData.addCommands(
    //   // 1. Install System Dependencies (ffmpeg, python, unzip)
    //   'apt-get update',
    //   'apt-get install -y python3-pip ffmpeg unzip',
      
    //   // 2. Install AWS CLI v2 (The Official Way)
    //   'curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"',
    //   'unzip awscliv2.zip',
    //   './aws/install',
    //   // Cleanup the installer files to keep things clean
    //   'rm -rf aws awscliv2.zip',

    //   // 3. Install Python Dependencies
    //   // --break-system-packages is needed on Ubuntu 24.04+ because Python is managed externally
    //   'pip3 install boto3 amazon-transcribe yt-dlp --break-system-packages',
      
    //   // 4. Download the Script from S3
    //   // Now 'aws' command is guaranteed to exist
    //   `aws s3 cp ${scriptAsset.s3ObjectUrl} /home/ubuntu/main.py`,
      
    //   // 5. Fix Permissions
    //   // 'aws s3 cp' runs as root, so we give the file to the 'ubuntu' user
    //   'chown ubuntu:ubuntu /home/ubuntu/main.py',
      
    //   // 6. Set Environment Variables
    //   `echo "export BUCKET_NAME=${bucket.bucketName}" >> /etc/environment`,
    //   `echo "export AWS_DEFAULT_REGION=${this.region}" >> /etc/environment`,
      
    //   // 7. Run the Script
    //   'su - ubuntu -c "source /etc/environment && python3 /home/ubuntu/main.py"'
    // );

    // 5. LAMBDA FUNCTION ( The Scout )
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
        INSTANCE_ID: soldier.instanceId,
      },
    });

    // Grant Permissions to Lambda
    scout.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ec2:DescribeInstances', 'ec2:StartInstances'],
      resources: ['*'], // Can be scoped down to soldier.instanceArn
    }));
    scout.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:PutParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/meeting/*`],
    }));

    // 6. SCHEDULER
    // Run every 15 minutes
    new events.Rule(this, 'ScoutSchedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
      targets: [new targets.LambdaFunction(scout)],
    });

    // Outputs
    new cdk.CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'InstanceId', { value: soldier.instanceId });
  }
}
