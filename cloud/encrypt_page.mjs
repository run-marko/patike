// Šifruje stranicu lozinkom za GitHub Pages, samo ugrađenim Node modulima (bez npm paketa).
// AES-256-GCM, ključ iz lozinke preko PBKDF2-SHA256 sa 600.000 ponavljanja i nasumičnom soli.
// Pregledač dešifruje istim algoritmom (WebCrypto). Bez lozinke stranica je samo nasumični bajtovi.
//
//   node cloud/encrypt_page.mjs <ulaz.html> <izlaz.html>     (lozinka iz promenljive PAGE_PASSWORD)
import { randomBytes, pbkdf2Sync, createCipheriv } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const [input, output] = process.argv.slice(2);
const password = process.env.PAGE_PASSWORD || "";
if (!input || !output) { console.error("upotreba: node cloud/encrypt_page.mjs ulaz.html izlaz.html"); process.exit(2); }
if (password.length < 8) { console.error("PAGE_PASSWORD mora imati bar 8 znakova"); process.exit(2); }

const ITER = 600000;
const salt = randomBytes(16), iv = randomBytes(12);
const key = pbkdf2Sync(password, salt, ITER, 32, "sha256");
const cipher = createCipheriv("aes-256-gcm", key, iv);
const body = Buffer.concat([cipher.update(readFileSync(input)), cipher.final(), cipher.getAuthTag()]);
const b64 = b => Buffer.from(b).toString("base64");

const page = `<!doctype html>
<html lang="sr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Patike</title>
<style>
:root{--page:#eaf1f8;--surface:#fff;--ink:#0b0b0b;--muted:#5d6a78;--accent:#256abf;--bad:#b52f2f;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--page:#0d1117;--surface:#1a1f26;--ink:#f0efec;--muted:#9aa5b1;--accent:#6da7ec;--bad:#e66767;color-scheme:dark}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);color:var(--ink);font:16px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}
form{background:var(--surface);padding:24px;border-radius:12px;width:100%;max-width:340px;display:grid;gap:12px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:20px;margin:0} p{margin:0;color:var(--muted);font-size:14px}
input[type=password]{font:inherit;padding:10px 12px;border:1px solid rgba(127,127,127,.4);border-radius:8px;background:var(--surface);color:var(--ink)}
label{display:flex;gap:8px;align-items:center;font-size:14px;color:var(--muted)}
button{font:inherit;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
#err{color:var(--bad);font-size:14px;min-height:1em}
</style>
</head>
<body>
<form id="f">
<h1>Patike</h1>
<p>Upiši lozinku za dashboard patika.</p>
<input id="pw" type="password" autocomplete="current-password" placeholder="Lozinka" required autofocus>
<label><input id="rem" type="checkbox" checked> Zapamti na ovom uređaju 30 dana</label>
<button>Otvori</button>
<div id="err" role="alert"></div>
</form>
<script>
const SALT="${b64(salt)}",IV="${b64(iv)}",DATA="${b64(body)}",ITER=${ITER},STORE="patike-kljuc";
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function keyFrom(pw){
  const base=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({name:"PBKDF2",hash:"SHA-256",salt:bytes(SALT),iterations:ITER},base,256));
}
async function open(raw){
  const key=await crypto.subtle.importKey("raw",raw,"AES-GCM",false,["decrypt"]);
  const html=new TextDecoder().decode(await crypto.subtle.decrypt({name:"AES-GCM",iv:bytes(IV)},key,bytes(DATA)));
  document.open();document.write(html);document.close();
}
// "Zapamti" čuva lozinku samo u ovom pregledaču (localStorage), jer stranica svakog dana dobija novu so,
// pa ključ izveden juče ne bi otvorio današnju stranicu.
(async()=>{
  try{
    const saved=JSON.parse(localStorage.getItem(STORE)||"null");
    if(saved&&saved.until>Date.now()){ await open(await keyFrom(saved.pw)); return; }
  }catch(e){ try{localStorage.removeItem(STORE);}catch(_){} }
})();
document.getElementById("f").addEventListener("submit",async e=>{
  e.preventDefault();
  const pw=document.getElementById("pw").value, err=document.getElementById("err");
  err.textContent="Otvaram...";
  try{
    const raw=await keyFrom(pw);
    if(document.getElementById("rem").checked){ try{localStorage.setItem(STORE,JSON.stringify({pw,until:Date.now()+30*864e5}));}catch(_){} }
    await open(raw);
  }catch(_){ err.textContent="Pogrešna lozinka."; }
});
</script>
</body>
</html>
`;
writeFileSync(output, page);
console.log(`šifrovano: ${output} (${Math.round(page.length / 1024)} KB)`);
