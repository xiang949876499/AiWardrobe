import { afterEach, describe, expect, test, vi } from "vitest";

import { compressImageFile } from "./imageCompression";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("compressImageFile", () => {
  test("returns original file for non-image uploads", async () => {
    const file = new File(["large text payload"], "notes.txt", { type: "text/plain" });

    await expect(compressImageFile(file, 1)).resolves.toBe(file);
  });

  test("returns original image when it is under the byte limit", async () => {
    const file = new File(["small"], "product.jpg", { type: "image/jpeg" });

    await expect(compressImageFile(file, 1024)).resolves.toBe(file);
  });

  test("returns original large non-JPEG image to preserve transparency", async () => {
    mockCanvasCompression(new Blob(["tiny"], { type: "image/jpeg" }));
    const file = new File(["large transparent image"], "product.png", { type: "image/png" });

    await expect(compressImageFile(file, 1)).resolves.toBe(file);
  });

  test("compresses a large JPEG to a smaller JPEG file when canvas succeeds", async () => {
    mockCanvasCompression(new Blob(["tiny"], { type: "image/jpeg" }));
    const file = new File(["large jpeg payload"], "product.jpeg", {
      type: "image/jpeg",
      lastModified: 123
    });

    const compressed = await compressImageFile(file, 1);

    expect(compressed).not.toBe(file);
    expect(compressed.name).toBe("product.jpg");
    expect(compressed.type).toBe("image/jpeg");
    expect(compressed.lastModified).toBe(123);
    expect(compressed.size).toBeLessThan(file.size);
  });

  test("returns original image when browser image APIs fail", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockRejectedValue(new Error("unsupported")));
    const file = new File(["large jpeg payload"], "product.jpg", { type: "image/jpeg" });

    await expect(compressImageFile(file, 1)).resolves.toBe(file);
  });
});

function mockCanvasCompression(blob: Blob) {
  const context = { drawImage: vi.fn() };
  const canvas = {
    width: 0,
    height: 0,
    getContext: vi.fn(() => context),
    toBlob: vi.fn((callback: BlobCallback) => callback(blob))
  } as unknown as HTMLCanvasElement;

  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({
    width: 640,
    height: 480,
    close: vi.fn()
  }));
  vi.spyOn(document, "createElement").mockReturnValue(canvas);
}
