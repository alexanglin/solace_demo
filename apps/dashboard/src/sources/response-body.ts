const DECIMAL_BYTES = /^(?:0|[1-9][0-9]*)$/;

/** Reads one response body without retaining bytes beyond the accepted boundary. */
export async function boundedUtf8Body(
  response: Response,
  maximumBytes: number,
): Promise<string | null> {
  const contentLength = response.headers.get("Content-Length");
  if (
    contentLength !== null &&
    (!DECIMAL_BYTES.test(contentLength) || Number(contentLength) > maximumBytes)
  ) {
    await response.body?.cancel();
    return null;
  }
  if (response.body === null) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let finished = false;
  while (!finished) {
    const next = await reader.read();
    if (next.done) {
      finished = true;
      continue;
    }
    size += next.value.byteLength;
    if (size > maximumBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(next.value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}
