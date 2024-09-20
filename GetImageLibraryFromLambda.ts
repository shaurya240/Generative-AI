import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import {
  S3Client,
  GetObjectCommand,
  ListObjectsCommand,
} from "@aws-sdk/client-s3";

export const handler = async (event, context) => {
  let assets = [];

  /**The bucket where the images are stored**/
  const assetsBucket = process.env.AssetsBucket;

  /**Get bucket objects**/
  const s3Client = new S3Client();
  const getAssetsCommand = new ListObjectsCommand({
    Bucket: assetsBucket,
    Prefix: "originals/",
  });

  try {
    const assetsResult = await s3Client.send(getAssetsCommand);

    if (assetsResult.$metadata.httpStatusCode == 200) {
      /**Generate a presigned URL for each image and their thumbnail**/
      for (let i = 0; i < assetsResult.Contents.length; i++) {
        let object = assetsResult.Contents[i];

        const signCommandOriginal = new GetObjectCommand({
          Bucket: assetsBucket,
          Key: object.Key,
        });
        const signCommandThumbnail = new GetObjectCommand({
          Bucket: assetsBucket,
          Key: object.Key.replace("originals/", "thumbs/"),
        });

        let preSignedUrlOriginal = await getSignedUrl(
          s3Client,
          signCommandOriginal,
          {
            expiresIn: 3600,
          }
        );
        let preSignedUrlThumbnail = await getSignedUrl(
          s3Client,
          signCommandThumbnail,
          {
            expiresIn: 3600,
          }
        );

        /**Add result to assets array**/
        assets.push({
          original: preSignedUrlOriginal,
          thumbnail: preSignedUrlThumbnail,
          s3ObjectBucket: assetsBucket,
          s3ObjectKey: object.Key.replace("originals/", "thumbs/")
        });
      }
      return {
        success: true,
        message: "Retrieved assets from bucket with name " + assetsBucket,
        result: assets,
      };
    } else {
      return {
        success: false,
        message: "Failed to retrieve asset library",
        result: assetsResult,
      };
    }
  } catch (ex) {
    return {
      success: false,
      message: "Error occurred while retrieving assets",
      result: ex.message,
    };
  }
};