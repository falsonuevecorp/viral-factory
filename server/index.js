const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const cookieParser = require('cookie-parser');
const jwt = require('jsonwebtoken');
const { initDB, getUserByEmail, getDb } = require('./database');

const app = express();
const PORT = process.env.PORT || 5000;
const HOST = '0.0.0.0';
const JWT_SECRET = 'viral-factory-secret-2026';

// Configuración de Multer
const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        if (!fs.existsSync('uploads')) fs.mkdirSync('uploads');
        cb(null, 'uploads/');
    },
    filename: (req, file, cb) => {
        cb(null, Date.now() + path.extname(file.originalname));
    }
});
const upload = multer({ storage: storage });

app.use(cookieParser());
app.use(express.json());

// --- AUTH MIDDLEWARE ---
const authMiddleware = (req, res, next) => {
    const publicPaths = ['/login.html', '/style.css', '/main.js', '/api/login'];
    if (publicPaths.includes(req.path)) return next();

    const token = req.cookies.token;
    if (!token) {
        if (req.path.startsWith('/api/')) return res.status(401).json({ error: 'Unauthorized' });
        return res.redirect('/login.html');
    }

    try {
        const decoded = jwt.verify(token, JWT_SECRET);
        req.user = decoded;
        next();
    } catch (err) {
        res.clearCookie('token');
        return res.redirect('/login.html');
    }
};

app.use(authMiddleware);
app.use(express.static(path.resolve(__dirname, '..')));

// --- ENDPOINTS ---
app.post('/api/login', async (req, res) => {
    const { email, password } = req.body;
    try {
        const user = await getUserByEmail(email);
        if (user && user.password === password) {
            const token = jwt.sign({ id: user.id, email: user.email }, JWT_SECRET, { expiresIn: '24h' });
            res.cookie('token', token, { httpOnly: true, maxAge: 24 * 60 * 60 * 1000 });
            return res.json({ success: true });
        }
        res.status(401).json({ success: false });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

const { spawn } = require('child_process');

// ... (después del middleware)

app.post('/api/process-video', upload.single('video'), async (req, res) => {
    if (!req.file) return res.status(400).send('No video file.');
    
    const templateName = req.body.template || 'hormozi';

    try {
        const db = getDb();
        const result = await db.run(
            'INSERT INTO videos (original_name, file_path, status) VALUES (?, ?, ?)',
            [req.file.originalname, req.file.path, 'processing']
        );
        const videoId = result.lastID;
        const outputPath = `uploads/output_${videoId}.mp4`;

        // Ejecutar motor IA en segundo plano
        const pythonProcess = spawn('python3', ['scripts/editor.py', req.file.path, outputPath, templateName]);
        let errorOutput = '';

        pythonProcess.stderr.on('data', (data) => {
            errorOutput += data.toString();
        });
        
        pythonProcess.on('close', async (code) => {
            const finalStatus = code === 0 ? 'completed' : 'failed';
            // Fallback si no logramos atrapar el stderr
            if (code !== 0 && !errorOutput) {
                errorOutput = 'Error desconocido en Python (Código ' + code + ')';
            }
            
            try {
                // Agregar la columna error_message si no existe (ignorará el error si ya existe)
                await db.run('ALTER TABLE videos ADD COLUMN error_message TEXT').catch(()=> {});
                
                await db.run('UPDATE videos SET status = ?, output_path = ?, error_message = ? WHERE id = ?', 
                    [finalStatus, outputPath, errorOutput, videoId]);
            } catch(e) {
                console.error("Error actualizando estado del video:", e);
            }
        });

        res.json({ success: true, videoId, message: 'Procesamiento iniciado' });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/api/videos', async (req, res) => {
    try {
        const db = getDb();
        const videos = await db.all('SELECT * FROM videos ORDER BY created_at DESC LIMIT 10');
        res.json(videos);
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.get('/api/download/:id', async (req, res) => {
    try {
        const db = getDb();
        const video = await db.get('SELECT * FROM videos WHERE id = ?', [req.params.id]);
        if (!video || !video.output_path || !fs.existsSync(video.output_path)) {
            return res.status(404).send('Video no encontrado o aún procesando.');
        }
        const ext = path.extname(video.original_name) || '.mp4';
        const baseName = path.basename(video.original_name, ext);
        const downloadName = `${baseName}_viral${ext}`;
        // res.download fuerza la cabecera Content-Disposition: attachment
        res.download(path.resolve(video.output_path), downloadName);
    } catch (e) {
        res.status(500).send(e.message);
    }
});

app.get('/health', (req, res) => res.status(200).send('OK'));

async function startServer() {
    await initDB();
    app.listen(PORT, HOST, () => {
        console.log(`🚀 ViralFactory Engine ONLINE on ${HOST}:${PORT}`);
    });
}

startServer();
