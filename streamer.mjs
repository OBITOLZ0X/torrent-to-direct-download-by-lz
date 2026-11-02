import WebTorrent from 'webtorrent';
import FormData from 'form-data';
import fs from 'fs';
import https from 'https';
import process from 'process';
import readline from 'readline';
import { PassThrough, Transform } from 'stream';

function emitError(message, code = 'ERROR') {
    const payload = JSON.stringify({ type: 'error', code, message });
    console.log(payload);
    console.error(payload);
}

// Global error protection to gracefully absorb benign WebTorrent internal swarm/peer race conditions
process.on('uncaughtException', (err) => {
    const msg = err?.message || String(err);
    if (
        msg.includes("Cannot read properties of null (reading 'private')") ||
        msg.includes("reading 'dht'") ||
        msg.includes('unexpected crypto handshake') ||
        msg.includes('unexpected handshake info hash') ||
        msg.includes('ECONNRESET') ||
        msg.includes('EPIPE') ||
        msg.includes('ETIMEDOUT')
    ) {
        return;
    }
    emitError(`Streamer process error: ${msg}`);
    process.exit(1);
});

process.on('unhandledRejection', (reason) => {
    const msg = reason?.message || String(reason);
    if (
        msg.includes('socket hang up') ||
        msg.includes('ECONNRESET') ||
        msg.includes('EPIPE') ||
        msg.includes('ETIMEDOUT') ||
        msg.includes('connection lost')
    ) {
        return;
    }
    emitError(`Unhandled rejection: ${msg}`);
});

// Parse CLI arguments
const args = process.argv.slice(2);
let source = '';
let fileIndex = 0;
let destination = 'buzzheavier';
let accountId = '';
let gofileToken = '';
let vikingToken = '';
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
    } else if (args[i] === '--viking-token' && args[i + 1]) {
        vikingToken = args[++i];
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
// Mode 1: Fast WebTorrent Metadata Resolver
// ─────────────────────────────────────────────────────────────
if (getMetadata) {
    const metaClient = new WebTorrent({
        dht: true,
        maxConns: 120,
    });

    let isDone = false;

    const timeoutTimer = setTimeout(() => {
        if (isDone) return;
        isDone = true;
        clearInterval(statusInterval);
        console.error(JSON.stringify({
            type: 'error',
            message: 'Could not fetch torrent metadata within timeout. Check peers/trackers.'
        }));
        metaClient.destroy(() => process.exit(1));
    }, 60000);

    const torrent = metaClient.add(torrentInput, { announce: extraTrackers });

    const statusInterval = setInterval(() => {
        if (isDone) {
            clearInterval(statusInterval);
            return;
        }
        console.log(JSON.stringify({
            type: 'meta_progress',
            peers: torrent.numPeers || 0
        }));
    }, 1000);

    torrent.on('metadata', () => {
        if (isDone) return;
        isDone = true;
        clearInterval(statusInterval);
        clearTimeout(timeoutTimer);
        try {
            if (outputPath) {
                fs.writeFileSync(outputPath, torrent.torrentFile);
            }
            console.log(JSON.stringify({
                type: 'metadata_done',
                name: torrent.name,
                total_size: torrent.length,
                files_count: torrent.files ? torrent.files.length : 1,
                output_path: outputPath
            }));
            metaClient.destroy(() => process.exit(0));
        } catch (e) {
            console.error(JSON.stringify({ type: 'error', message: `Failed to save torrent file: ${e.message}` }));
            metaClient.destroy(() => process.exit(1));
        }
    });

    torrent.on('error', (err) => {
        if (isDone) return;
        isDone = true;
        clearInterval(statusInterval);
        clearTimeout(timeoutTimer);
        console.error(JSON.stringify({ type: 'error', message: `Torrent error: ${err.message}` }));
        metaClient.destroy(() => process.exit(1));
    });

    metaClient.on('error', (err) => {
        if (isDone) return;
        isDone = true;
        clearInterval(statusInterval);
        clearTimeout(timeoutTimer);
        console.error(JSON.stringify({ type: 'error', message: `WebTorrent client error: ${err.message}` }));
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
            console.log(JSON.stringify({ type: 'paused' }));
        } else if (cmd === 'RESUME') {
            isStreamPaused = false;
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
    // Keeps pieces in RAM and safely reclaims pieces that have ALREADY passed the sequential read head,
    // ensuring "Chunk not found" never occurs and RAM usage is strictly capped (~64MB-100MB).
    let activeStore = null;

    function SlidingMemoryStore(chunkLength, opts) {
        if (!(this instanceof SlidingMemoryStore)) return new SlidingMemoryStore(chunkLength, opts);
        this.chunkLength = Number(chunkLength);
        this.chunks = new Map();
        this.closed = false;
        this.length = Number(opts?.length) || Infinity;
        if (this.length !== Infinity) {
            this.lastChunkLength = (this.length % this.chunkLength) || this.chunkLength;
            this.lastChunkIndex = Math.ceil(this.length / this.chunkLength) - 1;
        }
        activeStore = this;
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
        this.chunks.set(index, buf);
        queueMicrotask(() => cb(null));
    };

    SlidingMemoryStore.prototype.get = function (index, opts, cb = () => {}) {
        if (typeof opts === 'function') return this.get(index, null, opts);
        if (this.closed) return queueMicrotask(() => cb(new Error('Storage is closed')));

        let buf = this.chunks.get(index);
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

        queueMicrotask(() => cb(null, buf));
    };

    SlidingMemoryStore.prototype.cleanPastPieces = function (safePieceIndex) {
        if (this.closed || !this.chunks) return;
        for (const idx of this.chunks.keys()) {
            if (idx < safePieceIndex) {
                this.chunks.delete(idx);
            }
        }
    };

    SlidingMemoryStore.prototype.close = SlidingMemoryStore.prototype.destroy = function (cb = () => {}) {
        if (this.closed) return queueMicrotask(() => cb(new Error('Storage is closed')));
        this.closed = true;
        if (this.chunks) this.chunks.clear();
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

    // Helper to initialize multipart chunked upload on VikingFile.com
    function getVikingUploadUrls(size) {
        return new Promise((resolve, reject) => {
            const postData = `size=${size}`;
            const req = https.request('https://vikingfile.com/api/get-upload-url', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(postData),
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }, (res) => {
                let body = '';
                res.on('data', d => body += d);
                res.on('end', () => {
                    try {
                        const data = JSON.parse(body);
                        if (data && data.urls && data.urls.length > 0) {
                            resolve(data);
                        } else {
                            reject(new Error(`Invalid response from VikingFile get-upload-url: ${body}`));
                        }
                    } catch (e) {
                        reject(new Error(`Failed to parse VikingFile get-upload-url response: ${body}`));
                    }
                });
            });
            req.on('error', reject);
            req.write(postData);
            req.end();
        });
    }

    // Helper to complete multipart upload on VikingFile.com
    function completeVikingUpload(info, etags, filename, userToken = '') {
        return new Promise((resolve, reject) => {
            const params = new URLSearchParams();
            params.append('key', info.key);
            params.append('uploadId', info.uploadId);
            params.append('name', filename);
            params.append('user', userToken || '');
            etags.forEach((item, idx) => {
                params.append(`parts[${idx}][PartNumber]`, item.PartNumber);
                params.append(`parts[${idx}][ETag]`, item.ETag);
            });

            const postData = params.toString();
            const req = https.request('https://vikingfile.com/api/complete-upload', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(postData),
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }, (res) => {
                let body = '';
                res.on('data', d => body += d);
                res.on('end', () => {
                    try {
                        const data = JSON.parse(body);
                        if (!data || data.error || (!data.url && !data.hash)) {
                            const errMsg = (data && data.error) ? data.error : `Invalid response from VikingFile complete-upload: ${body}`;
                            return reject(new Error(errMsg));
                        }
                        resolve(data);
                    } catch (e) {
                        reject(new Error(`Failed to parse VikingFile complete-upload response: ${body}`));
                    }
                });
            });
            req.on('error', reject);
            req.write(postData);
            req.end();
        });
    }

    client.add(torrentInput, {
        store: SlidingMemoryStore,
        announce: extraTrackers,
        strategy: 'sequential'
    }, async (torrent) => {
        currentTorrent = torrent;

        if (fileIndex < 0 || fileIndex >= torrent.files.length) {
            console.error(JSON.stringify({
                type: 'error',
                message: `Invalid file index ${fileIndex}. Total files: ${torrent.files.length}`
            }));
            client.destroy();
            process.exit(1);
        }

        // Deselect other files so WebTorrent concentrates 100% on the selected file
        if (typeof torrent.deselect === 'function') {
            torrent.deselect(0, torrent.pieces.length - 1, false);
        }
        const file = torrent.files[fileIndex];
        file.select();
        if (typeof torrent.critical === 'function' && typeof file._startPiece === 'number') {
            const endCrit = Math.min(file._endPiece, file._startPiece + 1);
            torrent.critical(file._startPiece, endCrit);
        }
        const totalSize = file.length;

        console.log(JSON.stringify({
            type: 'start',
            filename: file.name,
            size: totalSize,
            destination: destination,
            peers: torrent.numPeers
        }));

        let bytesReadFromTorrent = 0;
        let bytesSentToNetwork = 0;
        let startTime = Date.now();
        let lastReport = 0;

        // Progress counting transform stream placed right before the network socket
        const networkCounter = new Transform({
            transform(chunk, encoding, callback) {
                bytesSentToNetwork += chunk.length;
                const now = Date.now();
                if (now - lastReport >= 1500 || bytesSentToNetwork >= totalSize) {
                    lastReport = now;
                    const elapsed = Math.max(0.001, (now - startTime) / 1000);
                    const speed = bytesSentToNetwork / elapsed;
                    const remaining = Math.max(0, totalSize - bytesSentToNetwork);
                    const eta = speed > 0 ? remaining / speed : 0;

                    console.log(JSON.stringify({
                        type: 'progress',
                        phase: 'transfer',
                        transferred: bytesSentToNetwork,
                        total: totalSize,
                        speed: speed,
                        eta: eta,
                        peers: torrent.numPeers,
                        isPaused: false
                    }));
                }
                this.push(chunk);
                callback();
            }
        });

        const uploadStream = new PassThrough({ highWaterMark: 16 * 1024 * 1024 });

        function pipeRawStreamIntoUpload(rawStream, firstChunk) {
            currentRawStream = rawStream;

            rawStream.on('error', (err) => {
                console.error(JSON.stringify({ type: 'error', message: `Torrent transfer error: ${err.message}` }));
                client.destroy();
                process.exit(1);
            });

            if (firstChunk) {
                bytesReadFromTorrent += firstChunk.length;
                uploadStream.write(firstChunk);
            }

            rawStream.on('data', (chunk) => {
                bytesReadFromTorrent += chunk.length;
                const pieceLen = torrent.pieceLength || (2 * 1024 * 1024);
                // Accurate global piece index taking into account file offset in multi-file torrents
                const currentTorrentPiece = Math.floor((file.offset + bytesReadFromTorrent) / pieceLen);

                // Free memory: keep at most 4 past pieces behind read head
                const safePiece = Math.max(0, currentTorrentPiece - 4);
                if (activeStore) {
                    activeStore.cleanPastPieces(safePiece);
                }

                const ok = uploadStream.write(chunk);
                if (!ok) {
                    rawStream.pause();
                    uploadStream.once('drain', () => {
                        rawStream.resume();
                    });
                }
            });

            rawStream.on('end', () => {
                uploadStream.end();
            });

            rawStream.resume();
        }

        // ─────────────────────────────────────────────
        // Route 1: Gofile.io Upload
        // ─────────────────────────────────────────────
        async function startGofileUpload(firstChunk, rawStream) {
            const servers = await getGofileServers();
            const serverName = (servers && servers.length > 0)
                ? servers[Math.floor(Math.random() * servers.length)]
                : 'store-eu-par-5';

            const form = new FormData();
            form.append('file', uploadStream.pipe(networkCounter), {
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
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
            headers['Accept'] = '*/*';
            headers['Connection'] = 'keep-alive';
            if (formLength) {
                headers['Content-Length'] = formLength;
            }

            const req = https.request({
                hostname: `${serverName}.gofile.io`,
                path: '/uploadFile',
                method: 'POST',
                headers: headers
            }, (res) => {
                let body = '';
                res.on('data', d => body += d);
                res.on('end', () => {
                    try {
                        const data = JSON.parse(body);
                        if (data.status === 'ok') {
                            const page = data.data?.downloadPage || (data.data?.parentFolderCode ? `https://gofile.io/d/${data.data.parentFolderCode}` : '');
                            if (page) {
                                console.log(JSON.stringify({
                                    type: 'done',
                                    status: res.statusCode,
                                    destination: 'gofile',
                                    url: page
                                }));
                                client.destroy(() => process.exit(0));
                                return;
                            }
                        }
                        const errMsg = data.status
                            ? `Gofile API returned: status=${data.status} ${JSON.stringify(data.data || {})}`
                            : `Gofile unexpected response (HTTP ${res.statusCode}): ${body.slice(0, 250)}`;
                        emitError(errMsg);
                        client.destroy(() => process.exit(1));
                    } catch (e) {
                        emitError(`Failed to parse Gofile response (HTTP ${res.statusCode}): ${body.slice(0, 250)}`);
                        client.destroy(() => process.exit(1));
                    }
                });
            });

            req.on('socket', (socket) => {
                socket.setKeepAlive(true, 15000);
                socket.setTimeout(0);
            });

            req.on('error', (err) => {
                const isConnClosed = err.message?.includes('socket hang up') ||
                                     err.code === 'ECONNRESET' ||
                                     err.code === 'ETIMEDOUT' ||
                                     err.message?.includes('timed out');
                emitError(isConnClosed ? `Gofile upload connection lost: ${err.message}` : `Gofile request error: ${err.message}`);
                client.destroy();
                process.exit(1);
            });

            form.pipe(req);
            startTime = Date.now();
            pipeRawStreamIntoUpload(rawStream, firstChunk);
        }

        // ─────────────────────────────────────────────
        // Route 2: Buzzheavier.com Upload (Default)
        // ─────────────────────────────────────────────
        async function startBuzzheavierUpload(firstChunk, rawStream) {
            const encodedName = encodeURIComponent(file.name);
            const headers = {
                'Content-Length': totalSize,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive',
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
                            client.destroy(() => process.exit(0));
                            return;
                        } catch (e) {
                            console.log(JSON.stringify({
                                type: 'done',
                                status: res.statusCode,
                                destination: 'buzzheavier',
                                raw: body
                            }));
                            client.destroy(() => process.exit(0));
                            return;
                        }
                    }
                    emitError(`Buzzheavier HTTP ${res.statusCode}: ${body.slice(0, 250)}`);
                    client.destroy(() => process.exit(1));
                });
            });

            req.on('socket', (socket) => {
                socket.setKeepAlive(true, 15000);
                socket.setTimeout(0);
            });

            req.on('error', (err) => {
                const isConnClosed = err.message?.includes('socket hang up') ||
                                     err.code === 'ECONNRESET' ||
                                     err.code === 'ETIMEDOUT' ||
                                     err.message?.includes('timed out');
                emitError(isConnClosed ? `Buzzheavier upload connection lost: ${err.message}` : `Buzzheavier request error: ${err.message}`);
                client.destroy();
                process.exit(1);
            });

            uploadStream.pipe(networkCounter).pipe(req);
            startTime = Date.now();
            pipeRawStreamIntoUpload(rawStream, firstChunk);
        }

        // ─────────────────────────────────────────────
        // Route 3: VikingFile.com Upload (Resumable S3 Multipart)
        // ─────────────────────────────────────────────
        async function startVikingfileUpload(firstChunk, rawStream) {
            currentRawStream = rawStream;
            let uploadInfo;
            try {
                uploadInfo = await getVikingUploadUrls(totalSize);
            } catch (err) {
                console.error(JSON.stringify({
                    type: 'error',
                    message: `Failed to initialize VikingFile upload: ${err.message}`
                }));
                client.destroy();
                process.exit(1);
            }

            const partSize = uploadInfo.partSize;
            const numberParts = uploadInfo.numberParts;
            const urls = uploadInfo.urls;

            function getPartLength(idx) {
                return (idx === numberParts - 1)
                    ? (totalSize - (partSize * (numberParts - 1)))
                    : partSize;
            }

            function createPartPut(url, pLen) {
                let partError = null;
                const u = new URL(url);
                const pass = new PassThrough({ highWaterMark: 16 * 1024 * 1024 });
                const promise = new Promise((resolve, reject) => {
                    const req = https.request({
                        protocol: u.protocol,
                        hostname: u.hostname,
                        path: u.pathname + u.search,
                        method: 'PUT',
                        headers: {
                            'Content-Length': pLen,
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                            'Accept': '*/*',
                            'Connection': 'keep-alive'
                        }
                    }, (res) => {
                        let body = '';
                        res.on('data', d => body += d);
                        res.on('end', () => {
                            if (res.statusCode === 200 || res.statusCode === 201) {
                                const rawEtag = res.headers['etag'] || '';
                                const etag = rawEtag.replace(/"/g, '');
                                resolve(etag);
                            } else {
                                const e = new Error(`VikingFile PUT part failed HTTP ${res.statusCode}: ${body}`);
                                partError = e;
                                reject(e);
                            }
                        });
                    });

                    req.on('socket', (socket) => {
                        socket.setKeepAlive(true, 15000);
                        socket.setTimeout(0);
                    });

                    req.on('error', (err) => {
                        const isConnClosed = err.message?.includes('socket hang up') ||
                                             err.code === 'ECONNRESET' ||
                                             err.code === 'ETIMEDOUT' ||
                                             err.message?.includes('timed out');
                        const e = new Error(isConnClosed ? `VikingFile upload connection lost: ${err.message}` : err.message);
                        partError = e;
                        reject(e);
                    });

                    pass.pipe(req);
                });

                // Attach no-op catch to prevent unhandled rejection crashes while in-flight
                promise.catch(() => {});

                return { pass, promise, getError: () => partError };
            }

            const etags = [];
            let currentPartIdx = 0;
            let bytesInCurrentPart = 0;
            let activePart = createPartPut(urls[0], getPartLength(0));

            startTime = Date.now();
            const progressInterval = setInterval(() => {
                const now = Date.now();
                const elapsed = Math.max(0.001, (now - startTime) / 1000);
                const speed = (isStreamPaused || elapsed <= 0) ? 0 : (bytesSentToNetwork / elapsed);
                const remaining = Math.max(0, totalSize - bytesSentToNetwork);
                const eta = (isStreamPaused || speed <= 0) ? 0 : (remaining / speed);

                console.log(JSON.stringify({
                    type: 'progress',
                    phase: 'transfer',
                    transferred: bytesSentToNetwork,
                    total: totalSize,
                    speed: speed,
                    eta: eta,
                    peers: torrent.numPeers,
                    isPaused: isStreamPaused
                }));
            }, 1500);

            async function* getChunks() {
                if (firstChunk) yield firstChunk;
                for await (const chunk of rawStream) {
                    yield chunk;
                }
            }

            try {
                for await (const chunk of getChunks()) {
                    while (isStreamPaused) {
                        await new Promise(r => setTimeout(r, 200));
                    }

                    if (activePart.getError()) {
                        // S3 closed idle connection while transfer was paused: re-open activePart cleanly
                        activePart = createPartPut(urls[currentPartIdx], getPartLength(currentPartIdx));
                        bytesInCurrentPart = 0;
                    }

                    bytesReadFromTorrent += chunk.length;
                    const pieceLen = torrent.pieceLength || (2 * 1024 * 1024);
                    const currentTorrentPiece = Math.floor((file.offset + bytesReadFromTorrent) / pieceLen);
                    const safePiece = Math.max(0, currentTorrentPiece - 4);
                    if (activeStore) {
                        activeStore.cleanPastPieces(safePiece);
                    }

                    let chunkOffset = 0;
                    while (chunkOffset < chunk.length) {
                        const expectedLen = getPartLength(currentPartIdx);
                        const remainingForPart = expectedLen - bytesInCurrentPart;
                        const availableFromChunk = chunk.length - chunkOffset;
                        const toWrite = Math.min(remainingForPart, availableFromChunk);

                        const slice = chunk.subarray(chunkOffset, chunkOffset + toWrite);
                        const canWrite = activePart.pass.write(slice);
                        if (!canWrite) {
                            await new Promise(resolve => activePart.pass.once('drain', resolve));
                        }
                        bytesInCurrentPart += toWrite;
                        bytesSentToNetwork += toWrite;
                        chunkOffset += toWrite;

                        if (bytesInCurrentPart === expectedLen) {
                            activePart.pass.end();
                            const etag = await activePart.promise;
                            etags.push({ PartNumber: currentPartIdx + 1, ETag: etag });

                            currentPartIdx++;
                            if (currentPartIdx < numberParts) {
                                activePart = createPartPut(urls[currentPartIdx], getPartLength(currentPartIdx));
                                bytesInCurrentPart = 0;
                            }
                        }
                    }
                }

                clearInterval(progressInterval);

                if (!etags || etags.length !== numberParts) {
                    throw new Error(`Incomplete multipart upload: only ${etags ? etags.length : 0} of ${numberParts} parts were uploaded.`);
                }

                const compResult = await completeVikingUpload(uploadInfo, etags, file.name, vikingToken);
                const pageUrl = compResult.url || (compResult.hash ? `https://vikingfile.com/f/${compResult.hash}` : '');
                if (!pageUrl || pageUrl.trim() === 'https://vikingfile.com/f/' || pageUrl.trim().endsWith('/f/')) {
                    throw new Error(`VikingFile returned an invalid or empty file URL: ${JSON.stringify(compResult)}`);
                }
                console.log(JSON.stringify({
                    type: 'done',
                    status: 200,
                    destination: 'vikingfile',
                    url: pageUrl,
                    hash: compResult.hash
                }));
                client.destroy(() => process.exit(0));
            } catch (err) {
                clearInterval(progressInterval);
                emitError(`VikingFile stream error: ${err.message}`);
                client.destroy();
                process.exit(1);
            }
        }

        // ─────────────────────────────────────────────
        // Buffer Initial Piece Before Opening HTTP Sockets
        // ─────────────────────────────────────────────
        const rawStream = file.createReadStream();
        currentRawStream = rawStream;

        let firstChunkReceived = false;
        const bufferingInterval = setInterval(() => {
            if (firstChunkReceived) {
                clearInterval(bufferingInterval);
                return;
            }
            let pLen = torrent.pieceLength || (4 * 1024 * 1024);
            let buffered = 0;
            try {
                if (file && typeof file._startPiece === 'number') {
                    const startIdx = file._startPiece;
                    if (torrent.pieces && startIdx === torrent.pieces.length - 1 && torrent.lastPieceLength) {
                        pLen = torrent.lastPieceLength;
                    }
                    if (torrent.bitfield && torrent.bitfield.get(startIdx)) {
                        buffered = pLen;
                    } else if (torrent.pieces && torrent.pieces[startIdx]) {
                        buffered = Math.max(0, pLen - (torrent.pieces[startIdx].missing || 0));
                    }
                }
            } catch (_) {}

            console.log(JSON.stringify({
                type: 'progress',
                phase: 'buffering',
                transferred: 0,
                total: totalSize,
                bufferedBytes: buffered,
                pieceLength: pLen,
                speed: torrent.downloadSpeed,
                eta: 0,
                peers: torrent.numPeers,
                isPaused: false
            }));
        }, 1500);

        rawStream.once('data', async (firstChunk) => {
            firstChunkReceived = true;
            clearInterval(bufferingInterval);
            rawStream.pause();

            if (destination === 'gofile') {
                await startGofileUpload(firstChunk, rawStream);
            } else if (destination === 'vikingfile') {
                await startVikingfileUpload(firstChunk, rawStream);
            } else {
                await startBuzzheavierUpload(firstChunk, rawStream);
            }
        });
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
