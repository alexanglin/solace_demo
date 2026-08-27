import { expect, test, vi } from "vitest";

import { boundedUtf8Body } from "./response-body";

test.each(["invalid", "4"])(
  "refuses the %s content-length before reading the body",
  async (value) => {
    // Arrange
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      cancel,
      start(controller) {
        controller.enqueue(new TextEncoder().encode("secret"));
      },
    });
    const response = new Response(body, { headers: { "Content-Length": value } });

    // Act
    const result = await boundedUtf8Body(response, 3);

    // Assert
    expect(result).toBeNull();
    expect(cancel).toHaveBeenCalledOnce();
  },
);

test("accepts an empty response without manufacturing bytes", async () => {
  // Arrange
  const response = new Response(null);

  // Act
  const result = await boundedUtf8Body(response, 3);

  // Assert
  expect(result).toBe("");
});

test("cancels a chunked body as soon as its cumulative bound is exceeded", async () => {
  // Arrange
  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([65, 66]));
        controller.enqueue(new Uint8Array([67, 68]));
      },
    }),
  );

  // Act
  const result = await boundedUtf8Body(response, 3);

  // Assert
  expect(result).toBeNull();
});

test("joins bounded chunks and decodes only strict UTF-8", async () => {
  // Arrange
  const valid = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0x61]));
        controller.enqueue(new Uint8Array([0xc3, 0xa9]));
        controller.close();
      },
    }),
  );
  const invalid = new Response(new Uint8Array([0xff]));

  // Act
  const validResult = await boundedUtf8Body(valid, 3);
  const invalidResult = await boundedUtf8Body(invalid, 1);

  // Assert
  expect(validResult).toBe("aé");
  expect(invalidResult).toBeNull();
});
