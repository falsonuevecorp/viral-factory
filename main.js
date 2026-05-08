document.getElementById('drop-zone').addEventListener('click', (e) => {
    console.log('Drop-zone clicked!');
    document.getElementById('file-input').click();
});

document.getElementById('file-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        console.log('File selected:', file.name);
        handleUpload(file);
    }
});

async function handleUpload(file) {
    const formData = new FormData();
    formData.append('video', file);

    // Mostrar estado de carga en el dropzone
    const dropZone = document.getElementById('drop-zone');
    const originalHTML = dropZone.innerHTML;
    dropZone.innerHTML = '<div style="text-align: center; color: white;"><h3>Subiendo video...</h3></div>';
    dropZone.style.pointerEvents = 'none';

    try {
        const response = await fetch('/api/process-video', {
            method: 'POST',
            body: formData
        });

        // Intentar parsear como JSON, si no, como texto para errores del servidor web (ej. 413)
        let result;
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") !== -1) {
            result = await response.json();
        } else {
            const textError = await response.text();
            throw new Error('Error del servidor: ' + response.status + ' - ' + textError.substring(0, 50));
        }
        
        console.log('Server response:', result);
        
        if (result.success) {
            // Limpiar input y volver a mostrar dropzone original
            document.getElementById('file-input').value = '';
            dropZone.innerHTML = originalHTML;
            dropZone.style.pointerEvents = 'auto';
            
            // Recargar la lista de videos para ver el nuevo en 'processing'
            if (typeof loadVideos === 'function') {
                loadVideos();
            }
        } else {
            throw new Error(result.error || 'Upload failed sin detalles');
        }
    } catch (error) {
        console.error('Error uploading video:', error);
        alert('Error al subir video: ' + error.message);
        dropZone.innerHTML = originalHTML;
        dropZone.style.pointerEvents = 'auto';
    }
}

async function loadVideos() {
    try {
        const response = await fetch('/api/videos');
        if (!response.ok) return;
        const videos = await response.json();
        
        const videoList = document.getElementById('video-list');
        const videoCount = document.getElementById('video-count');
        
        videoCount.innerText = `${videos.length} videos`;
        
        if (videos.length === 0) {
            videoList.innerHTML = '<p class="text-zinc-500 text-center py-10">No hay videos en la cola.</p>';
            return;
        }

        videoList.innerHTML = videos.map(video => `
            <div class="video-item" style="border: 1px solid #333; padding: 10px; margin-bottom: 10px; border-radius: 8px; background: #111;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h4 style="margin: 0; color: #fff;">${video.original_name}</h4>
                    <span style="padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; 
                        background: ${video.status === 'completed' ? '#22c55e22' : video.status === 'failed' ? '#ef444422' : '#eab30822'};
                        color: ${video.status === 'completed' ? '#22c55e' : video.status === 'failed' ? '#ef4444' : '#eab308'};">
                        ${video.status.toUpperCase()}
                    </span>
                </div>
                ${video.status === 'completed' ? `<a href="/${video.output_path}" target="_blank" style="color: #3b82f6; font-size: 14px; text-decoration: none; margin-top: 8px; display: inline-block;">Ver Resultado</a>` : ''}
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading videos:', error);
    }
}

// Cargar videos al iniciar
document.addEventListener('DOMContentLoaded', loadVideos);

// Polling cada 5 segundos para actualizar estados
setInterval(loadVideos, 5000);

