FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}

RUN pip install -r requirements.txt

RUN playwright install chromium

COPY scout_lambda.py ${LAMBDA_TASK_ROOT}

CMD ["scout_lambda.lambda_handler"]

