import { readFile } from "node:fs/promises";
import { Sandbox, Drive } from "@vercel/sandbox";

let pending,
  cached,
  validUntil = 0;
async function initialize() {
  const drive = await Drive.getOrCreate({
    name: "safenet-data",
    maxSize: 1024 ** 3,
    region: "iad1",
  });
  const resume = async (box) => {
    const command = await box.runCommand("sh", [
      "-c",
      "if test -f /vercel/sandbox/app/.ready; then /vercel/sandbox/app/.venv/bin/python /vercel/sandbox/app/cloud/boot.py; fi",
    ]);
    if (command.exitCode !== 0) throw new Error("Не удалось запустить API");
  };
  const box = await Sandbox.getOrCreate({
    name: "safenet-runtime",
    persistent: true,
    snapshotExpiration: 7 * 24 * 3600 * 1000,
    mounts: { "/data": drive },
    ports: [8000, 8501],
    resources: { vcpus: 2 },
    timeout: 600000,
    resume: true,
    onResume: resume,
  });
  const marker = await box.readFileToBuffer({
    path: "/vercel/sandbox/app/.ready",
  });
  if (marker === null) {
    await box.writeFiles([
      {
        path: "/vercel/sandbox/backend.tgz",
        content: await readFile(
          new URL("./private/backend.tgz", import.meta.url),
        ),
      },
      {
        path: "/vercel/sandbox/setup.sh",
        content: await readFile(new URL("./setup.sh", import.meta.url)),
        mode: 0o700,
      },
    ]);
    const command = await box.runCommand("bash", [
      "-c",
      "flock /tmp/safenet-install.lock bash /vercel/sandbox/setup.sh",
    ]);
    if (command.exitCode !== 0)
      throw new Error("Не удалось подготовить сервер");
  } else await resume(box);
  if (box.expiresAt && box.expiresAt.getTime() - Date.now() < 120000) {
    try {
      await box.extendTimeout(300000);
    } catch {
      /* Сеанс возобновится из сохранённого состояния. */
    }
  }
  const key = await box.readFileToBuffer({ path: "/data/.access-token" });
  if (!key) throw new Error("Отсутствует ключ шлюза");
  return { origin: box.domain(8000), key: key.toString().trim() };
}

export async function runtime() {
  if (cached && Date.now() < validUntil) return cached;
  if (!pending)
    pending = initialize().finally(() => {
      pending = undefined;
    });
  cached = await pending;
  validUntil = Date.now() + 30000;
  return cached;
}

export function invalidateRuntime() {
  cached = undefined;
  validUntil = 0;
}
