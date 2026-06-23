export async function compressImageFile(file: File, maxBytes = 2 * 1024 * 1024): Promise<File> {
  if (!file.type.startsWith("image/") || file.size <= maxBytes || file.type !== "image/jpeg") return file;

  try {
    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;

    const context = canvas.getContext("2d");
    if (!context) return file;

    context.drawImage(bitmap, 0, 0);
    bitmap.close?.();

    let smallestBlob: Blob | null = null;

    for (const quality of [0.82, 0.72, 0.62, 0.52]) {
      const blob = await canvasToBlob(canvas, "image/jpeg", quality);
      if (blob && blob.size < file.size) {
        if (blob.size <= maxBytes) return jpegFileFromBlob(blob, file);
        if (!smallestBlob || blob.size < smallestBlob.size) smallestBlob = blob;
      }
    }

    if (smallestBlob) return jpegFileFromBlob(smallestBlob, file);
  } catch {
    return file;
  }

  return file;
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, type, quality);
  });
}

function jpegFileFromBlob(blob: Blob, source: File): File {
  return new File([blob], withJpegExtension(source.name), {
    type: "image/jpeg",
    lastModified: source.lastModified
  });
}

function withJpegExtension(name: string): string {
  return name.replace(/\.[^.]+$/, "") + ".jpg";
}
