const sqlite3 = require('sqlite3').verbose();
const { open } = require('sqlite');

let db;

async function initDB() {
    db = await open({
        filename: './server/viral.db',
        driver: sqlite3.Database
    });

    // Tabla de Videos y Procesamiento
    await db.exec(`CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_name TEXT,
        file_path TEXT,
        status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
        output_path TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`);

    // Tabla de Calendario de Publicación
    await db.exec(`CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        platform TEXT, -- instagram, tiktok, youtube
        scheduled_for DATETIME,
        status TEXT DEFAULT 'scheduled', -- scheduled, published, error
        caption TEXT,
        FOREIGN KEY(video_id) REFERENCES videos(id)
    )`);

    // Tabla de Configuración (API Keys de RRSS)
    await db.exec(`CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        ayrshare_api_key TEXT,
        post_frequency TEXT DEFAULT 'daily', -- daily, weekly
        post_time TEXT DEFAULT '18:00',
        brand_style TEXT DEFAULT 'apple'
    )`);

    await db.exec(`INSERT OR IGNORE INTO settings (id) VALUES (1)`);
    // Tabla de Usuarios
    await db.exec(`CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        password TEXT,
        name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`);

    // Insertar usuario administrador por defecto
    const user = await db.get('SELECT * FROM users WHERE email = ?', ['admin@viral.ai']);
    if (!user) {
        await db.run('INSERT INTO users (email, password, name) VALUES (?, ?, ?)', 
            ['admin@viral.ai', 'elite2026', 'Viral Factory CEO']);
    }
    
    console.log("📦 ViralFactory DB: Sincronizada y Blindada");
}

async function getUserByEmail(email) {
    return await db.get('SELECT * FROM users WHERE email = ?', [email]);
}

module.exports = { initDB, getDb: () => db, getUserByEmail };
