# Upload Modes Spec

## Goal
Upload supports two paths:

- Plain upload: user uploads one garment/item photo. The backend stores the image only, does not call VL/ComfyUI/RunningHub, and creates one `pending_review` item for manual tags.
- Auto recognition: user uploads an outfit/multi-item photo. The backend creates an upload session, reserves recognition billing through a no-op billing hook for now, then calls the vision workflow to split/tag items.

## Categories
Closet categories are:

- `top`
- `bottom`
- `outerwear`
- `shoes`
- `bag`
- `accessory`

`bag` is separate from `accessory`; scarves, belts, jewelry, hats remain `accessory`.

## API
- `POST /uploads/plain-garment`: single-item upload, no AI calls, returns one `Garment` with `status=pending_review` and `review_status=pending_review`.
- `POST /uploads/garment-photo`: auto recognition path. Keeps existing response shape and calls the existing detection/tagging workflow. It also calls `BillingService.reserve_upload_recognition(...)` before workflow execution.

## UI
Upload page uses a segmented control:

- Plain upload: copy says this requires one item per image and manual confirmation.
- Auto recognition: copy says it can handle full outfit/multi-item photos and may consume recognition quota.

Both paths show pending items and send users to the existing detail confirmation flow.
