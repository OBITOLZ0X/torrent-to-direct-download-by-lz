import WebTorrent from 'webtorrent';
import FormData from 'form-data';
import fs from 'fs';
import https from 'https';
import process from 'process';
import readline from 'readline';
import { PassThrough } from 'stream';

// Parse CLI arguments
const args = process.argv.slice(2);
let source = '';
let fileIndex = 0;
let destination = 'buzzheavier';
let accountId = '';
let gofileToken = '';
let getMetadata = false;
let outputPath = '';

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
    } else if (args[i] === '--get-metadata') {
        getMetadata = true;
    } else if (args[i] === '--output' && args[i + 1]) {
        outputPath = args[++i];
    }
}

if (!source) {
    console.error(JSON.stringify({ type: 'error', message: 'Missing --source argument' }));
    process.exit(1);
}

// Best high-speed public trackers to maximize peer and swarm discovery
const extraTrackers = [
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://open.stealth.si:80/announce',
    'udp://tracker.torrent.eu.org:451/announce',
    'udp://tracker.bittor.pw:1337/announce',
    'udp://tracker.dler.org:6969/announce',
    'udp://exodus.desync.com:6969/announce',
    'udp://open.demonii.com:1337/announce',
    'http://tracker.openbittorrent.com:80/announce',
    'udp://tracker.moeking.me:6969/announce',
    'udp://explodie.org:6969/announce',
    'udp://opentracker.i2p.rocks:6969/announce',
    'udp://open.tracker.cl:1337/announce',
    'udp://tracker.cyberia.is:6969/announce',
    'udp://tracker.qu.ax:6969/announce',
    'udp://opentor.net:6969/announce',
];

// Prepare torrent input
let torrentInput = source;
if (fs.existsSync(source)) {
    try {
        torrentInput = fs.readFileSync(source);
    } catch (_) {
        torrentInput = source;
    }
}

// ─────────────────────────────────────────────────────────────
// Mode 1: Fast WebTorrent Metadata Resolver Fallback
// ─────────────────────────────────────────────────────────────
if (getMetadata) {
    console.log(JSON.stringify({ type: 'status', message: 'Resolving magnet metadata with WebTorrent engine...' }));
    const metaClient = new WebTorrent({
        dht: true,
        maxConns: 120,
    });

    const timeoutTimer = setTimeout(() => {
        console.error(JSON.stringify({
            type: 'error',
            message: 'WebTorrent metadata fetch timed out. Check seeders/trackers.'
        }));
        metaClient.destroy(() => process.exit(1));
    }, 45000);

    metaClient.add(torrentInput, { announce: extraTrackers }, (torrent) => {
        clearTimeout(timeoutTimer);
        try {
            if (outputPath) {
                fs.writeFileSync(outputPath, torrent.torrentFile);
            }
            console.log(JSON.stringify({
                type: 'metadata_done',
                name: torrent.name,
                total_size: torrent.length,
                files_count: torrent.files.length,
                output_path: outputPath
            }));
            metaClient.destroy(() => process.exit(0));
        } catch (e) {
            console.error(JSON.stringify({ type: 'error', message: `Failed to save torrent file: ${e.message}` }));
            metaClient.destroy(() => process.exit(1));
        }
    });

    metaClient.on('error', (err) => {
        clearTimeout(timeoutTimer);
        console.error(JSON.stringify({ type: 'error', message: err.message }));
        metaClient.destroy(() => process.exit(1));
    });
} else {
    // ─────────────────────────────────────────────────────────────
    // Mode 2: Live Memory Streaming to Cloud Host
    // ─────────────────────────────────────────────────────────────

    // Interactive Stdin Command Handler (PAUSE / RESUME / STOP)
    let isStreamPaused = false;
    let currentRawStream = null;
    let currentTorrent = null;

    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
        terminal: false
    });

    rl.on('line', (line) => {
        const cmd = line.trim().toUpperCase();
        if (cmd === 'PAUSE') {
            isStreamPaused = true;
            if (currentTorrent && typeof currentTorrent.pause === 'function') {
                try { currentTorrent.pause(); } catch (_) {}
            }
            if (currentRawStream && typeof currentRawStream.pause === 'function') {
                try { currentRawStream.pause(); } catch (_) {}
            }
            console.log(JSON.stringify({ type: 'paused' }));
        } else if (cmd === 'RESUME') {
            isStreamPaused = false;
            if (currentTorrent && typeof currentTorrent.resume === 'function') {
                try { currentTorrent.resume(); } catch (_) {}
            }
            if (currentRawStream && typeof currentRawStream.resume === 'function') {
                try { currentRawStream.resume(); } catch (_) {}
            }
            console.log(JSON.stringify({ type: 'resumed' }));
        } else if (cmd === 'STOP') {
            if (client) {
                client.destroy(() => process.exit(0));
            } else {
                process.exit(0);
            }
        }
    });

    // Custom Sliding-Window Memory Chunk Store
    // Automatically frees past chunks from RAM once read, preventing high memory usage and GC stalls on large multi-GB files.
    function SlidingMemoryStore(chunkLength, opts) {
        if (!(this instanceof SlidingMemoryStore)) return new SlidingMemoryStore(chunkLength, opts);
        this.chunkLength = Number(chunkLength);
        this.chunks = [];
        this.closed = false;
        this.length = Number(opts?.length) || Infinity;
        if (this.length !== Infinity) {
            this.lastChunkLength = (this.length % this.chunkLength) || this.chunkLength;
            this.lastChunkIndex = Math.ceil(this.length / this.chunkLength) - 1;
        }
    }

    SlidingMemoryStore.prototype.put = function (index, buf, cb = () => {}) {
        if (this.closed) return queueMicrotask(() => cb(new Error('Storage is closed')));
        const isLastChunk = (index === this.lastChunkIndex);
        if (isLastChunk && buf.length !== this.lastChunkLength) {
            return queueMicrotask(() => cb(new Error('Last chunk length must be ' + this.lastChunkLength)));
        }
        if (!isLastChunk && buf.length !== this.chunkLength) {
            return queueMicrotask(() => cb(new Error('Chunk length must be ' + this.chunkLength)));
        }
        this.chunks[index] = buf;
        queueMicrotask(() => cb(null));
    };

    SlidingMemoryStore.prototype.get = function (index, opts, cb = () => {}) {
        if (typeof opts === 'function') return this.get(index, null, opts);
        if (this.closed) return queueMicrotask(() => cb(new Error('Storage is closed')));

        let buf = this.chunks[index];
        if (!buf) {
            const err = new Error('Chunk not found');
            err.notFound = true;
            return queueMicrotask(() => cb(err));
        }

        if (!opts) opts = {};
        const offset = opts.offset || 0;
        const len = opts.length || (buf.length - offset);

        if (offset !== 0 || len !== buf.length) {
            buf = buf.slice(offset, len + offset);
        }

        // Free older pieces to keep RAM under ~30MB even for 50GB torrents
        if (index > 4 && this.chunks && this.chunks[index - 4]) {
            this.chunks[index - 4] = null;
            delete this.chunks[index - 4];
        }

        queueMicrotask(() => cb(null, buf));
    };

    SlidingMemoryStore.prototype.close = SlidingMemoryStore.prototype.destroy = function (cb = () => {}) {
        if (this.closed) return queueMicrotask(() => cb(new Error('Storage is closed')));
        this.closed = true;
        this.chunks = null;
        queueMicrotask(() => cb(null));
    };

    const client = new WebTorrent({
        dht: true,
        maxConns: 85,
    });

    console.log(JSON.stringify({ type: 'status', message: 'Connecting to swarm and caching in RAM...' }));

    // Helper to query available Gofile servers
    function getGofileServers() {
        return new Promise((resolve) => {
            https.get('https://api.gofile.io/servers', (res) => {
                let body = '';
                res.on('data', d => body += d);
                res.on('end', () => {
                    try {
                        const data = JSON.parse(body);
                        if (data.status === 'ok' && data.data?.servers?.length > 0) {
                            resolve(data.data.servers.map(s => s.name));
                        } else {
                            resolve(['store-eu-par-5', 'store-na-phx-3', 'store8']);
                        }
                    } catch (e) {
                        resolve(['store-eu-par-5', 'store-na-phx-3', 'store8']);
                    }
                });
            }).on('error', () => {
                resolve(['store-eu-par-5', 'store-na-phx-3', 'store8']);
            });
        });
    }

    client.add(torrentInput, { store: SlidingMemoryStore, announce: extraTrackers }, async (torrent) => {
        currentTorrent = torrent;

        if (fileIndex < 0 || fileIndex >= torrent.files.length) {
            console.error(JSON.stringify({
                type: 'error',
                message: `Invalid file index ${fileIndex}. Total files: ${torrent.files.length}`
            }));
            client.destroy();
            process.exit(1);
        }

        const file = torrent.files[fileIndex];
        file.select();

        const totalSize = file.length;

        console.log(JSON.stringify({
            type: 'start',
            filename: file.name,
            size: totalSize,
            destination: destination,
            peers: torrent.numPeers
        }));

        const rawStream = file.createReadStream();
        currentRawStream = rawStream;
        const uploadStream = new PassThrough();

        let transferred = 0;
        let startTime = Date.now();
        let lastReport = 0;

        uploadStream.on('data', (chunk) => {
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
                    peers: torrent.numPeers,
                    isPaused: isStreamPaused
                }));
            }
        });

        rawStream.on('error', (err) => {
            console.error(JSON.stringify({ type: 'error', message: `Torrent stream error: ${err.message}` }));
            client.destroy();
            process.exit(1);
        });

        // In-RAM Prebuffer: Hold initial chunks up to 4MB (or totalSize if smaller) before opening remote HTTP socket
        const PREBUFFER_LIMIT = Math.min(totalSize, 4 * 1024 * 1024);
        const prebufferChunks = [];
        let bufferedBytes = 0;
        let uploadStarted = false;

        const peerInterval = setInterval(() => {
            if (!uploadStarted) {
                console.log(JSON.stringify({
                    type: 'progress',
                    transferred: 0,
                    total: totalSize,
                    speed: torrent.downloadSpeed || 0,
                    eta: 0,
                    peers: torrent.numPeers,
                    isPaused: isStreamPaused
                }));
            }
        }, 2000);

        function startUpload() {
            if (uploadStarted) return;
            uploadStarted = true;
            clearInterval(peerInterval);
            startTime = Date.now();

            if (destination === 'gofile') {
                startGofileUpload();
            } else {
                startBuzzheavierUpload();
            }
        }

        rawStream.on('data', (chunk) => {
            if (isStreamPaused) {
                rawStream.pause();
                return;
            }

            if (!uploadStarted) {
                prebufferChunks.push(chunk);
                bufferedBytes += chunk.length;
                if (bufferedBytes >= PREBUFFER_LIMIT) {
                    startUpload();
                }
            } else {
                const ok = uploadStream.write(chunk);
                if (!ok) {
                    rawStream.pause();
                    uploadStream.once('drain', () => {
                        if (!isStreamPaused) {
                            rawStream.resume();
                        }
                    });
                }
            }
        });

        rawStream.on('end', () => {
            if (!uploadStarted) {
                startUpload();
            } else {
                uploadStream.end();
            }
        });

        // ─────────────────────────────────────────────
        // Route 1: Gofile.io Upload
        // ─────────────────────────────────────────────
        async function startGofileUpload() {
            const servers = await getGofileServers();
            const serverName = servers[0] || 'store-eu-par-5';

            const form = new FormData();
            form.append('file', uploadStream, {
                filename: file.name,
                knownLength: totalSize
            });
            if (gofileToken) {
                form.append('token', gofileToken);
            }

            const formLength = await new Promise((resolve) => {
                form.getLength((err, len) => resolve(err ? null : len));
            });

            const headers = form.getHeaders();
            if (formLength) {
                headers['Content-Length'] = formLength;
            }

            // Connection agent without hard 5-minute timeout
            const agent = new https.Agent({
                keepAlive: true,
                keepAliveMsecs: 15000
            });

            const req = https.request({
                hostname: `${serverName}.gofile.io`,
                path: '/uploadFile',
                method: 'POST',
                headers: headers,
                agent: agent
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

            // Dynamic inactivity timer: only triggers if ZERO data received or sent for 120s while NOT paused
            let inactivityTimer = null;
            function resetGofileInactivity() {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                inactivityTimer = setTimeout(() => {
                    if (!isStreamPaused) {
                        console.error(JSON.stringify({
                            type: 'error',
                            isConnectionClosed: true,
                            wasPaused: false,
                            message: 'Gofile upload connection stalled: No data transferred for 120 seconds.'
                        }));
                        req.destroy();
                    }
                }, 120000);
            }
            resetGofileInactivity();

            uploadStream.on('data', () => resetGofileInactivity());

            req.on('error', (err) => {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                const isConnClosed = err.message?.includes('socket hang up') ||
                                     err.code === 'ECONNRESET' ||
                                     err.code === 'ETIMEDOUT' ||
                                     err.message?.includes('timed out');
                console.error(JSON.stringify({
                    type: 'error',
                    code: err.code || 'REQUEST_ERROR',
                    isConnectionClosed: isConnClosed,
                    wasPaused: isStreamPaused,
                    message: isConnClosed
                        ? (isStreamPaused
                            ? 'Connection closed by Gofile while paused.'
                            : `Gofile upload connection lost: ${err.message}`)
                        : `Gofile request error: ${err.message}`
                }));
                client.destroy();
                process.exit(1);
            });

            form.pipe(req);

            // Flush all prebuffered chunks into uploadStream
            for (const c of prebufferChunks) {
                uploadStream.write(c);
            }
            prebufferChunks.length = 0;

            if (bufferedBytes >= totalSize) {
                uploadStream.end();
            }
        }

        // ─────────────────────────────────────────────
        // Route 2: Buzzheavier.com Upload (Default)
        // ─────────────────────────────────────────────
        async function startBuzzheavierUpload() {
            const encodedName = encodeURIComponent(file.name);
            const headers = {
                'Content-Length': totalSize
            };
            if (accountId) {
                headers['Authorization'] = `Bearer ${accountId}`;
            }

            // Connection agent without hard 5-minute timeout
            const agent = new https.Agent({
                keepAlive: true,
                keepAliveMsecs: 15000
            });

            const req = https.request({
                hostname: 'w.buzzheavier.com',
                path: '/' + encodedName,
                method: 'PUT',
                headers: headers,
                agent: agent
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

            // Dynamic inactivity timer: only triggers if ZERO data received or sent for 120s while NOT paused
            let inactivityTimer = null;
            function resetBuzzInactivity() {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                inactivityTimer = setTimeout(() => {
                    if (!isStreamPaused) {
                        console.error(JSON.stringify({
                            type: 'error',
                            isConnectionClosed: true,
                            wasPaused: false,
                            message: 'Buzzheavier upload connection stalled: No data transferred for 120 seconds.'
                        }));
                        req.destroy();
                    }
                }, 120000);
            }
            resetBuzzInactivity();

            uploadStream.on('data', () => resetBuzzInactivity());

            req.on('error', (err) => {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                const isConnClosed = err.message?.includes('socket hang up') ||
                                     err.code === 'ECONNRESET' ||
                                     err.code === 'ETIMEDOUT' ||
                                     err.message?.includes('timed out');
                console.error(JSON.stringify({
                    type: 'error',
                    code: err.code || 'REQUEST_ERROR',
                    isConnectionClosed: isConnClosed,
                    wasPaused: isStreamPaused,
                    message: isConnClosed
                        ? (isStreamPaused
                            ? 'Connection closed by Buzzheavier while paused.'
                            : `Buzzheavier upload connection lost: ${err.message}`)
                        : `Buzzheavier request error: ${err.message}`
                }));
                client.destroy();
                process.exit(1);
            });

            uploadStream.pipe(req);

            // Flush all prebuffered chunks into uploadStream
            for (const c of prebufferChunks) {
                uploadStream.write(c);
            }
            prebufferChunks.length = 0;

            if (bufferedBytes >= totalSize) {
                uploadStream.end();
            }
        }
    });

    // Handle termination
    process.on('SIGINT', () => {
        if (client) client.destroy(() => process.exit(0));
        else process.exit(0);
    });
    process.on('SIGTERM', () => {
        if (client) client.destroy(() => process.exit(0));
        else process.exit(0);
    });
}
