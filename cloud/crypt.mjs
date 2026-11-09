// Šifruje i dešifruje fajl stanja (baza, keš, sličice) lozinkom, samo ugrađenim Node modulima.
// Repozitorijum je javan, pa se stanje čuva samo šifrovano: AES-256-GCM, ključ iz lozinke (PBKDF2-SHA256, 600.000).
// Format: "PTK1" | so (16) | iv (12) | šifrovano | oznaka (16).
//
//   node cloud/crypt.mjs enc <ulaz> <izlaz>     (lozinka iz promenljive PAGE_PASSWORD)
//   node cloud/crypt.mjs dec <ulaz> <izlaz>
import { randomBytes, pbkdf2Sync, createCipheriv, createDecipheriv } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const MAGIC = Buffer.from("PTK1");
const ITER = 600000;
const [mode, input, output] = process.argv.slice(2);
const password = process.env.PAGE_PASSWORD || "";
if (!["enc", "dec"].includes(mode) || !input || !output) {
  console.error("upotreba: node cloud/crypt.mjs enc|dec ulaz izlaz"); process.exit(2);
}
if (password.length < 8) { console.error("PAGE_PASSWORD mora imati bar 8 znakova"); process.exit(2); }

const data = readFileSync(input);
if (mode === "enc") {
  const salt = randomBytes(16), iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", pbkdf2Sync(password, salt, ITER, 32, "sha256"), iv);
  writeFileSync(output, Buffer.concat([MAGIC, salt, iv, cipher.update(data), cipher.final(), cipher.getAuthTag()]));
} else {
  if (!data.subarray(0, 4).equals(MAGIC)) { console.error("fajl nije šifrovano stanje"); process.exit(3); }
  const salt = data.subarray(4, 20), iv = data.subarray(20, 32), tag = data.subarray(data.length - 16);
  const decipher = createDecipheriv("aes-256-gcm", pbkdf2Sync(password, salt, ITER, 32, "sha256"), iv);
  decipher.setAuthTag(tag);
  try {
    writeFileSync(output, Buffer.concat([decipher.update(data.subarray(32, data.length - 16)), decipher.final()]));
  } catch {
    console.error("pogrešna lozinka ili oštećen fajl"); process.exit(4);
  }
}
console.log(`${mode === "enc" ? "šifrovano" : "dešifrovano"}: ${output}`);
