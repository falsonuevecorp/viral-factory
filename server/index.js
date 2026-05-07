const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const cookieParser = require('cookie-parser');
const jwt = require('jsonwebtoken');
const { initDB, getUserByEmail } = require('./database');

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
app.use(express.static(path.join(__dirname, '../')));

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

app.post('/api/process-video', upload.single('video'), (req, res) => {
    if (!req.file) return res.status(400).send('No video file.');
    res.json({ success: true, videoId: req.file.filename });
});

app.get('/health', (req, res) => res.status(200).send('OK'));

async function startServer() {
    await initDB();
    app.listen(PORT, HOST, () => {
        console.log(`🚀 ViralFactory Engine ONLINE on ${HOST}:${PORT}`);
    });
}

startServer();
