# My AI Agent

## Image Attachments and Vision Support

### Application Upload Limits

The application currently enforces these limits per chat message:

- Maximum 4 images per message.
- Maximum 6 MB per image.
- Maximum 16 MB combined across all images in one message.
- Supported formats: PNG, JPEG, and WEBP.
- Image data is stored with the conversation in the SQLite database and is available when reopening the conversation.

There is currently **no application-enforced daily image-upload limit**. These limits apply per message, not per day.

### Provider and Model Limits

The absence of an application-level daily upload limit does not mean image processing is unlimited.

Each API provider may impose its own:
- Requests-per-minute and requests-per-day quotas.
- Input-token limits.
- Image size, resolution, and processing restrictions.
- Model-specific availability and usage limits.

Images sent to a vision model consume provider resources and may count toward its input-token usage or other applicable quotas.

### How Image Recognition Works

The application sends attached images directly to the selected model using an OpenAI-compatible multimodal message format.

A multimodal user message contains:
- Text content, when provided.
- Images encoded as data URLs in `image_url` content blocks.

**Vision-capable models**
- Receive both the user's text and attached images.
- Can interpret image contents, answer questions about images, read visible text, and perform other supported visual tasks.

**Text-only models**
- Cannot interpret image contents.
- The application checks the selected model's image capability before sending an image request.
- If the model does not support image input, the application rejects the request and asks the user to select a vision-capable model.

### No Automatic Image-to-Text Fallback

The application does not currently run a separate image-recognition service to describe an image before sending it to a text-only model.

In particular, it does not automatically:
1. Send the image to a vision model.
2. Convert the image into a textual description.
3. Forward that description to the selected text-only model.

Image understanding therefore requires selecting a vision-capable model.

