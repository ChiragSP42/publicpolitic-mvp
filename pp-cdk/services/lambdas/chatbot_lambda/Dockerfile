<<<<<<< HEAD
FROM public.ecr.aws/lambda/python:3.13
=======
FROM public.ecr.aws/lambda/python:latest
>>>>>>> main

COPY requirements.txt ${LAMBDA_TASK_ROOT}

RUN pip install -r requirements.txt

COPY chatbot_lambda.py ${LAMBDA_TASK_ROOT}

CMD ["chatbot_lambda.lambda_handler"]