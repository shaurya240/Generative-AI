import boto3
import json
import os
from typing import Dict, List

REGION = os.environ['AWS_REGION']
BEDROCK_ENDPOINT = f'https://bedrock-runtime.{REGION}.amazonaws.com'
MODEL_ID = 'anthropic.claude-3-5-sonnet-20240620-v1:0'

def detect_labels(rekognition_results: Dict) -> Dict:
    color_arr = [row['HexCode'] for row in rekognition_results['ImageProperties']['DominantColors']]
    return {
        'labels': rekognition_results['Labels'],
        'domColors': color_arr
    }

def parse_labels(labels: List[Dict]) -> str:
    return '\n'.join(label['Name'] for label in labels)

def generate_prompt(ad_context: str, labels: str) -> str:
    return (
        f'The following objects are present in an image for an advertisement for {ad_context}:\n'
        f'<objects>\n{labels}\n</objects>\n'
        'Based on the objects listed above, generate a pitch, a Google display font, and a tagline '
        'to go with the image in an advertisement. Your response should be in JSON format, following '
        'this schema: {"tagline":"string", "font":"string", "pitch":"string"}. Keep the pitch under '
        '50 words. Do not include any text in the response outside of the JSON object. Use double '
        'quotes for property names.'
    )

def invoke_bedrock(bedrock_client, prompt: str) -> Dict:
    system = [{'text': 'You are a marketing and advertising expert. You are great at creating advertising assets, selecting the right words and colors for ads.'}]
    inference_config = {'maxTokens': 2048}
    messages = [{'role': 'user', 'content': [{'text': prompt}]}]

    response = bedrock_client.converse(
        modelId=MODEL_ID,
        messages=messages,
        system=system,
        inferenceConfig=inference_config
    )
    return response

def parse_bedrock_response(response: Dict) -> Dict:
    string_answer = response['output']['message']['content'][0]['text']
    json_start = string_answer.find('{')
    json_end = string_answer.rfind('}') + 1
    return json.loads(string_answer[json_start:json_end])

def lambda_handler(event: Dict, context) -> Dict:
    bedrock = boto3.client(service_name='bedrock-runtime', region_name=REGION, endpoint_url=BEDROCK_ENDPOINT)

    ad_context, image_style = event['context'].split('_', 1) if '_' in event['context'] else (event['context'], 'photographic')
    requestor_id = event['requestorId']
    rekognition_results = event['rekognitionResults']

    image_info = detect_labels(rekognition_results)
    labels_text = parse_labels(image_info['labels'])
    prompt = generate_prompt(ad_context, labels_text)
    print(f"Generated prompt: {prompt}")

    bedrock_response = invoke_bedrock(bedrock, prompt)
    structured_answer = parse_bedrock_response(bedrock_response)
    structured_answer['domColors'] = image_info['domColors']
    structured_answer['originalPrompt'] = prompt
    print(f"Structured answer: {structured_answer}")

    lambda_client = boto3.client('lambda')
    account = context.invoked_function_arn.split(':')[4]
    lambda_to_invoke = f"arn:aws:lambda:{REGION}:{account}:function:{os.environ.get('PublishResultsViaAppSyncLambda').strip()}"

    payload = {
        'id': requestor_id,
        'content': json.dumps(structured_answer),
        'entities': json.dumps(image_info['labels']),
        'domain': 'ad-copy-generation',
        'prompt': f"{ad_context};{image_style}",
        'source_type': 'bedrock',
        'source_id': 'anthropic.claude-v2'
    }
    lambda_payload = json.dumps(payload).encode('utf-8')
    lambda_client.invoke(FunctionName=lambda_to_invoke, InvocationType='Event', Payload=lambda_payload)

    return event