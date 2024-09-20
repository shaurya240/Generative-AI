import base64
import json
import logging
import random
import uuid
import os
from datetime import date

import boto3
from botocore.exceptions import ClientError

def create_image_generation_body(imagery_type, prompt, style_preset, colors=[], model_override=''):
    """
    Create the body for image generation based on the given parameters.
    """
    negative_prompts = [
        "ugly", "duplicate", "morbid", "mutilated", "out of frame",
        "extra fingers", "mutated hands", "poorly drawn hands", "poorly drawn face",
        "mutation", "deformed", "blurry", "bad anatomy", "bad proportions",
        "extra limbs", "cloned face", "disfigured", "gross proportions",
        "malformed limbs", "missing arms", "missing legs", "extra arms",
        "extra legs", "fused fingers", "too many fingers", "long neck", "nude"
    ]

    width, height = (768, 1152) if imagery_type == "feature-image" else (768, 768) #needed for task 4 TIG G1 v2
    aspect_ratio = "4:5" if imagery_type == "feature_image" else "1:1" #needed for SD3 Large

    negative_text = ", ".join(negative_prompts)
    
    body_json = {
        "prompt": f'{style_preset}-style image of {prompt}',
        "mode": "text-to-image",
        "aspect_ratio": aspect_ratio,
        "seed": random.randint(0, 4294967295),
    }
    if len(model_override)>0:
        body_json = {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {
                "text": prompt,
                "negativeText": negative_text
            },
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "seed": random.randint(0, 214783647),
                "width": width,
                "height": height
            }
    }

    # Task 4 - Change the above body_json to
    '''
    body_json = {
        "taskType": "COLOR_GUIDED_GENERATION",
        "colorGuidedGenerationParams": {
            "text": prompt,
            "negativeText": negative_text,
            "colors": colors
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "seed": random.randint(0, 214783647),
            "width": width,
            "height": height
        }
    }
    '''

    return json.dumps(body_json)

def save_moodboard_image(table, id, prompt, fullprompt, original, thumbnail, type, bucket, key, asset_type, style):
    """
    Add images to the moodboard history table.
    """
    item = {
        "id": uuid.uuid4().hex,
        "moodboard_id": id,
        "fullprompt": fullprompt,
        "prompt": prompt,
        'generated_date': str(date.today()),
        'base64': "",
        "original": original,
        "thumbnail": thumbnail,
        'part_type': type,
        'bucket': bucket,
        'key': key,
        'assetType': asset_type,
        'style': style
    }

    dynamo = boto3.resource('dynamodb').Table(table)
    response = dynamo.put_item(Item=item)

    if response["ResponseMetadata"]["HTTPStatusCode"] == 200:
        return item
    else:
        raise Exception('Failed to persist image part.')

def upload_image(session_id, region, data, file_name, bucket, exists=False):
    """
    Upload an image to S3 and return a presigned URL.
    """
    try:
        s3 = boto3.resource("s3")
        if not exists:
            s3.Bucket(bucket).put_object(Key=file_name, Body=data)
        return create_presigned_url(region, bucket, file_name, 604800)
    except Exception as e:
        logging.error(f"The image couldn't be saved: {str(e)}")
        return ''

def create_presigned_url(region, bucket_name, object_name, expiration=604800):
    """
    Generate a presigned URL for the S3 object.
    """
    s3_client = boto3.client('s3', region_name=region)
    try:
        return s3_client.generate_presigned_url('get_object',
                                                Params={'Bucket': bucket_name, 'Key': object_name},
                                                ExpiresIn=expiration)
    except ClientError as e:
        logging.error(e)
        return ""

def lambda_handler(event, context):
    """
    Main Lambda function handler.
    """
    region = context.invoked_function_arn.split(':')[3]
    acct = context.invoked_function_arn.split(':')[4]
    style_preset = event.get('style_preset', 'photographic')
    bucket = os.environ.get('ImageBucket', '').strip()
    table = os.environ.get('MoodboardHistoryTableName')
    publish_results_lambda = f'arn:aws:lambda:{region}:{acct}:function:{os.environ.get("PublishResultsViaAppSyncLambda", "").strip()}'
    
    bedrock = boto3.client(service_name='bedrock-runtime', region_name=region, 
                           endpoint_url=f'https://bedrock-runtime.{region}.amazonaws.com')
    
    search_type = event["type"]
    asset_type = event.get('assetType', 'advertising-moodboard')
    
    # Task 4 - change model to 'amazon.titan-image-generator-v2:0'
    model_override = os.environ.get('ModelOverride', '').strip()
    model_id = 'stability.sd3-large-v1:0' if len(model_override)==0 else model_override
    # model_id = 'amazon.titan-image-generator-v2:0'
    
    color_scheme = json.loads(event.get('color_scheme', '[]'))
    
    for term in event['terms']:
        image_prompt = create_image_generation_body(search_type, term, style_preset, color_scheme, model_override)
        
        response = bedrock.invoke_model(body=image_prompt, modelId=model_id, accept='application/json', contentType='application/json')
        response_body = json.loads(response.get('body').read())
        
        images = response_body.get('images')
        
        image_b64 = images[0]
        
        img_data = base64.b64decode(image_b64.encode())
        key = f"{term.replace(',', '').replace(' ', '_')}__{event['spec'].replace(' ', '_')}{event['id']}.png"
        file_url = upload_image(event["id"], region, img_data, key, bucket, False)
        
        result = [{
            "base64": "",
            "thumbnail": file_url,
            "partType": search_type,
            "original": file_url,
            "prompt": term,
            "fullprompt": {"text_prompts": [{"text": term}]},
            "finishReason": "SUCCESS"
        }]
        
        try:
            dynamo_doc = save_moodboard_image(table, event['id'], term, image_prompt, file_url, file_url, 
                                              search_type, bucket, key, asset_type, style_preset)
            result[0]['assetPartId'] = dynamo_doc['id']
            result[0]['assetId'] = event['id']
        
            if search_type == 'google_images':
                search_type = 'imagery'
            
            payload = {
                'id': event['id'],
                'content': json.dumps({"type": search_type, "results": result}),
                'entities': json.dumps(event['terms']),
                'domain': asset_type,
                'prompt': term,
                'source_type': 'bedrock',
                'source_id': model_id
            }
            
            lambda_client = boto3.client('lambda')
            lambda_client.invoke(FunctionName=publish_results_lambda,
                                 InvocationType='Event',
                                 Payload=json.dumps(payload).encode('utf-8'))
        except Exception as e:
            logging.error(f"Failed to generate image: {str(e)}")
            return {
                'statusCode': 400,
                'message': 'Failed to generate image'
            }
    
    return {
        'statusCode': 200,
        'body': payload
    }