#%%
import boto3
import os
from dotenv import load_dotenv
load_dotenv(override=True)

AWS_ACCESS_KEY = os.getenv("LOCAL_AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("LOCAL_AWS_SECRET_KEY")
AWS_REGION = 'us-west-2'

session = boto3.Session(aws_access_key_id=AWS_ACCESS_KEY,
                        aws_secret_access_key=AWS_SECRET_KEY,
                        region_name=AWS_REGION)

bedrock = session.client("bedrock", region_name=AWS_REGION)

bedrock.list_foundation_models(byProvider='Anthropic')
# %%
