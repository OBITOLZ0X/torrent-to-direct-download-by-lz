import WebTorrent from 'webtorrent';
import memoryChunkStore from 'memory-chunk-store';
import FormData from 'form-data';
import fs from 'fs';
import https from 'https';
import process from 'process';

// Parse CLI arguments
const args = process.argv.slice(2);
let source = '';
let fileIndex = 0;
let destination = 'buzzheavier';
let accountId = '';
let gofileToken = '';

for (let i = 0; i < args.length; i++) {
    if (args[i] === '--source' && args[i + 1]) {
        source = args[++i];
    } else if (args[i] === '--file-index' && args[i + 1]) {
        fileIndex = parseInt(args[++i], 10);
    } else if (args[i] === '--destination' && args[i + 1]) {
        destination = args[++i].toLowerCase();
    } else if (args[i] === '--account-id' && args[i + 1]) {
        accountId = args[++i];
    } else if (args[i] === '--gofile-token' && args[i + 1]) {
        gofileToken = args[++i];
    }
}

if (!source) {
    console.error(JSON.stringify({ type: 'error', message: 'Missing --source argument' }));
    process.exit(1);
}

// Prepare torrent input
let torrentInput = source;
if (fs.existsSync(source)) {
    torrentInput = fs.readFileSync(source);
}

const client = new WebTorrent({
    dht: true,
    maxConns: 55,
});

// Best public trackers to accelerate swarm discovery
const extraTrackers = [
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://open.stealth.si:80/announce',
    'udp://tracker.torrent.eu.org:451/announce',
    'udp://tracker.bittor.pw:1337/announce',
    'udp://tracker.dler.org:6969/announce',
    'udp://exodus.desync.com:6969/announce',
    'udp://open.demonii.com:1337/announce',
    'http://tracker.openbittorrent.com:80/announce'
];

console.log(JSON.stringify({ type: 'status', message: 'Connecting to swarm and caching in RAM...' }));

// Helper to query best Gofile server
function getBestGofileServer() {
    return new Promise((resolve, reject) => {
        https.get('https://api.gofile.io/servers', (res) => {
            let body = '';
            res.on('data', d => body += d);
            res.on('end', () => {
                try {
                    const data = JSON.parse(body);
                    if (data.status === 'ok' && data.data?.servers?.length > 0) {
                        resolve(data.data.servers[0].name);
                    } else {
                        resolve('store1');
                    }
                } catch (e) {
                    resolve('store1');
                }
            });
        }).on('error', (err) => {
            resolve('store1');
        });
    });
}

client.add(torrentInput, { store: memoryChunkStore, announce: extraTrackers }, async (torrent) => {
    if (fileIndex < 0 || fileIndex >= torrent.files.length) {
        console.error(JSON.stringify({
            type: 'error',
            message: `Invalid file index ${fileIndex}. Total files: ${torrent.files.length}`
        }));
        client.destroy();
        process.exit(1);
    }

    const file = torrent.files[fileIndex];
    const totalSize = file.length;

    console.log(JSON.stringify({
        type: 'start',
        filename: file.name,
        size: totalSize,
        destination: destination,
        peers: torrent.numPeers
    }));

    const stream = file.createReadStream();
    let transferred = 0;
    let startTime = Date.now();
    let lastReport = 0;

    // Monitor progress
    stream.on('data', (chunk) => {
        transferred += chunk.length;
        const now = Date.now();
        if (now - lastReport >= 1500 || transferred === totalSize) {
            lastReport = now;
            const elapsed = Math.max(0.001, (now - startTime) / 1000);
            const speed = transferred / elapsed;
            const remaining = Math.max(0, totalSize - transferred);
            const eta = speed > 0 ? remaining / speed : 0;

            console.log(JSON.stringify({
                type: 'progress',
                transferred: transferred,
                total: totalSize,
                speed: speed,
                eta: eta,
                peers: torrent.numPeers
            }));
        }
    });

    stream.on('error', (err) => {
        console.error(JSON.stringify({ type: 'error', message: `Stream error: ${err.message}` }));
        client.destroy();
        process.exit(1);
    });

    // ─────────────────────────────────────────────
    // Route 1: Gofile.io Upload
    // ─────────────────────────────────────────────
    if (destination === 'gofile') {
        const serverName = await getBestGofileServer();
        const form = new FormData();
        form.append('file', stream, {
            filename: file.name,
            knownLength: totalSize
        });
        if (gofileToken) {
            form.append('token', gofileToken);
        }

        const req = https.request({
            hostname: `${serverName}.gofile.io`,
            path: '/uploadFile',
            method: 'POST',
            headers: form.getHeaders()
        }, (res) => {
            let body = '';
            res.on('data', d => body += d);
            res.on('end', () => {
                try {
                    const data = JSON.parse(body);
                    if (data.status === 'ok') {
                        const page = data.data?.downloadPage || `https://gofile.io/d/${data.data?.parentFolderCode || ''}`;
                        console.log(JSON.stringify({
                            type: 'done',
                            status: res.statusCode,
                            destination: 'gofile',
                            url: page
                        }));
                    } else {
                        console.error(JSON.stringify({
                            type: 'error',
                            message: `Gofile API returned status: ${data.status}`
                        }));
                    }
                } catch (e) {
                    console.error(JSON.stringify({
                        type: 'error',
                        message: `Failed to parse Gofile response: ${body}`
                    }));
                }
                client.destroy(() => process.exit(0));
            });
        });

        req.on('error', (err) => {
            console.error(JSON.stringify({ type: 'error', message: `Gofile request error: ${err.message}` }));
            client.destroy();
            process.exit(1);
        });

        form.pipe(req);

    // ─────────────────────────────────────────────
    // Route 2: Buzzheavier.com Upload (Default)
    // ─────────────────────────────────────────────
    } else {
        const encodedName = encodeURIComponent(file.name);
        const headers = {
            'Content-Length': totalSize
        };
        if (accountId) {
            headers['Authorization'] = `Bearer ${accountId}`;
        }

        const req = https.request({
            hostname: 'w.buzzheavier.com',
            path: '/' + encodedName,
            method: 'PUT',
            headers: headers
        }, (res) => {
            let body = '';
            res.on('data', chunk => body += chunk);
            res.on('end', () => {
                if (res.statusCode === 200 || res.statusCode === 201) {
                    try {
                        const data = JSON.parse(body);
                        const fileId = data?.data?.id;
                        const buzzUrl = fileId ? `https://buzzheavier.com/${fileId}` : 'https://buzzheavier.com/';
                        console.log(JSON.stringify({
                            type: 'done',
                            status: res.statusCode,
                            destination: 'buzzheavier',
                            id: fileId,
                            url: buzzUrl
                        }));
                    } catch (e) {
                        console.log(JSON.stringify({
                            type: 'done',
                            status: res.statusCode,
                            destination: 'buzzheavier',
                            raw: body
                        }));
                    }
                } else {
                    console.error(JSON.stringify({
                        type: 'error',
                        message: `Buzzheavier HTTP ${res.statusCode}: ${body}`
                    }));
                }
                client.destroy(() => process.exit(0));
            });
        });

        req.on('error', (err) => {
            console.error(JSON.stringify({ type: 'error', message: `Buzzheavier request error: ${err.message}` }));
            client.destroy();
            process.exit(1);
        });

        stream.pipe(req);
    }
});

// Handle termination
process.on('SIGINT', () => client.destroy(() => process.exit(0)));
process.on('SIGTERM', () => client.destroy(() => process.exit(0)));
