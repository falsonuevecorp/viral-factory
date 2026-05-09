let selectedTemplate = 'hormozi';

// --- UI Interaction ---

document.querySelectorAll('.template-option').forEach(el => {
    el.addEventListener('click', () => {
        // Remove active state from all
        document.querySelectorAll('.template-option').forEach(opt => {
            opt.querySelector('.template-indicator').classList.add('hidden');
            opt.querySelector('.template-label').classList.remove('text-primary-fixed-dim');
            opt.querySelector('.template-label').classList.add('text-on-surface-variant');
            const bgDiv = opt.querySelector('.aspect-video');
            bgDiv.classList.remove('border-primary-fixed-dim');
            bgDiv.classList.add('border-outline-variant');
        });

        // Add active state to clicked
        el.querySelector('.template-indicator').classList.remove('hidden');
        el.querySelector('.template-label').classList.add('text-primary-fixed-dim');
        el.querySelector('.template-label').classList.remove('text-on-surface-variant');
        const bgDiv = el.querySelector('.aspect-video');
        bgDiv.classList.add('border-primary-fixed-dim');
        bgDiv.classList.remove('border-outline-variant');

        selectedTemplate = el.getAttribute('data-template');
        console.log("Template seleccionado:", selectedTemplate);
    });
});

// Upload button binding
const uploadBtn = document.getElementById('upload-btn');
const fileInput = document.getElementById('file-input');

uploadBtn.addEventListener('click', () => {
    fileInput.click();
});

fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        handleUpload(file);
    }
});

// --- API Logic ---

async function handleUpload(file) {
    const formData = new FormData();
    formData.append('video', file);
    formData.append('template', selectedTemplate); // Send the selected template

    // Mostrar UI de carga
    const progressOverlay = document.getElementById('upload-progress-overlay');
    progressOverlay.classList.remove('hidden');
    progressOverlay.classList.add('flex');
    uploadBtn.style.pointerEvents = 'none';

    try {
        const response = await fetch('/api/process-video', {
            method: 'POST',
            body: formData
        });

        let result;
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") !== -1) {
            result = await response.json();
        } else {
            const textError = await response.text();
            throw new Error('Error del servidor: ' + response.status + ' - ' + textError.substring(0, 50));
        }
        
        if (result.success) {
            fileInput.value = '';
            progressOverlay.classList.add('hidden');
            progressOverlay.classList.remove('flex');
            uploadBtn.style.pointerEvents = 'auto';
            
            if (typeof loadVideos === 'function') {
                loadVideos();
            }
        } else {
            throw new Error(result.error || 'Upload failed sin detalles');
        }
    } catch (error) {
        console.error('Error uploading video:', error);
        alert('Error al subir video: ' + error.message);
        progressOverlay.classList.add('hidden');
        progressOverlay.classList.remove('flex');
        uploadBtn.style.pointerEvents = 'auto';
    }
}

async function loadVideos() {
    try {
        const response = await fetch('/api/videos');
        if (!response.ok) return;
        const videos = await response.json();
        
        const videoList = document.getElementById('video-list');
        const videoCount = document.getElementById('video-count');
        
        videoCount.innerText = `${videos.length} VIDEOS`;
        
        if (videos.length === 0) {
            videoList.innerHTML = '<p class="text-on-surface-variant/50 text-center py-10 font-label-sm text-xs mt-10">NO HAY VIDEOS EN LA COLA</p>';
            return;
        }

        // Render as Timeline Clips
        videoList.innerHTML = videos.map(video => {
            const isCompleted = video.status === 'completed';
            const isFailed = video.status === 'failed';
            
            let statusColor = '#eab308'; // Processing (Yellow)
            let bgClass = 'bg-surface';
            let borderClass = 'border-outline-variant';
            
            if (isCompleted) {
                statusColor = '#00f0ff'; // Completed (Cyan/Primary)
                bgClass = 'bg-surface-bright';
                borderClass = 'border-primary-fixed-dim';
            } else if (isFailed) {
                statusColor = '#ffb4ab'; // Failed (Error)
                borderClass = 'border-error';
            }

            return `
                <div class="flex gap-unit w-full relative items-center min-h-[48px] group" onclick="${isCompleted ? `previewVideo('/${video.output_path}')` : ''}">
                    <div class="h-12 w-full ${bgClass} border ${borderClass} rounded shrink-0 relative overflow-hidden flex items-center px-3 ${isCompleted ? 'cursor-pointer hover:bg-surface-container-high transition-colors' : ''}">
                        
                        <!-- Magnetic Handles for visual flair -->
                        <div class="absolute left-0 top-0 bottom-0 w-2 border-r ${isCompleted ? 'border-primary-fixed-dim bg-primary-fixed-dim/10' : 'border-outline-variant bg-surface-container'}"></div>
                        
                        <div class="ml-3 flex flex-col justify-center flex-grow">
                            <span class="font-label-sm text-label-sm ${isCompleted ? 'text-primary-fixed-dim' : 'text-on-surface'} truncate drop-shadow-sm">${video.original_name}</span>
                            <span class="font-timecode text-[10px] uppercase" style="color: ${statusColor}">${video.status} ${isFailed ? '- HAZ CLIC PARA VER ERROR' : ''}</span>
                        </div>

                        ${isCompleted ? `
                            <button class="bg-primary-fixed-dim text-black font-label-sm px-2 py-1 rounded text-[10px] ml-auto hover:bg-white transition-colors" onclick="event.stopPropagation(); window.open('/${video.output_path}', '_blank')">DESCARGAR</button>
                        ` : ''}
                    </div>
                </div>
                ${isFailed && video.error_message ? `
                    <div class="bg-error-container text-on-error-container p-2 mt-1 rounded text-xs font-mono mb-2 overflow-x-auto w-full">
                        ${video.error_message}
                    </div>
                ` : ''}
            `;
        }).join('');
    } catch (error) {
        console.error('Error loading videos:', error);
    }
}

// Function to preview video in the main canvas
function previewVideo(url) {
    const player = document.getElementById('main-video-player');
    const placeholder = document.getElementById('video-placeholder');
    
    placeholder.style.display = 'none';
    player.style.display = 'block';
    player.src = url;
    player.play();
}

// Cargar videos al iniciar
document.addEventListener('DOMContentLoaded', loadVideos);

// Polling cada 5 segundos para actualizar estados
setInterval(loadVideos, 5000);
