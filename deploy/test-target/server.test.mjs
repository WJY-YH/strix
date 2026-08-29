import assert from "node:assert/strict";
import test from "node:test";

import { createFixtureServer } from "./server.mjs";


test("fixture exposes a deterministic reflected marker", async () => {
  const server = createFixtureServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    const response = await fetch(
      `http://127.0.0.1:${address.port}/?name=%3Cstrix-fixture%3E`,
    );
    assert.equal(response.status, 200);
    assert.match(await response.text(), /<strix-fixture>/);
    assert.equal(response.headers.has("content-security-policy"), false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});


test("fixture exposes health and rejects unknown paths", async () => {
  const server = createFixtureServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    const root = `http://127.0.0.1:${address.port}`;
    assert.equal((await fetch(`${root}/health`)).status, 200);
    assert.equal((await fetch(`${root}/unknown`)).status, 404);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
