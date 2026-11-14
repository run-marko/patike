import {readFileSync,appendFileSync} from 'node:fs';
import {privateDecrypt,createDecipheriv,constants} from 'node:crypto';
const [privatePath,configPath]=process.argv.slice(2);
const packet=JSON.parse(readFileSync(configPath,'utf8'));
if(packet.version!==1) throw new Error('Unknown collector credential format');
const key=privateDecrypt({key:readFileSync(privatePath),oaepHash:'sha256',padding:constants.RSA_PKCS1_OAEP_PADDING},Buffer.from(packet.key,'base64'));
const decipher=createDecipheriv('aes-256-gcm',key,Buffer.from(packet.iv,'base64'));
decipher.setAuthTag(Buffer.from(packet.tag,'base64'));
const data=JSON.parse(Buffer.concat([decipher.update(Buffer.from(packet.payload,'base64')),decipher.final()]).toString('utf8'));
if(data.origin!=='https://marko-direct-collector.marko-aksic85.chatgpt.site'||typeof data.token!=='string'||!data.token||/[\r\n]/.test(data.token)) throw new Error('Invalid collector credential');
if(!process.env.GITHUB_ENV) throw new Error('Workflow environment missing');
// The token is masked before it becomes available to later workflow steps.
console.log('::add-mask::'+data.token);
appendFileSync(process.env.GITHUB_ENV,'HOSTED_COLLECTOR_TOKEN='+data.token+'\n','utf8');
