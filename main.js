document.getElementById('drop-zone').addEventListener('click', () => {
    document.getElementById('file-input').click();
});

document.getElementById('file-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        handleUpload(file);
    }
});

async function handleUpload(file) {
    const formData = new FormData();
    formData.append('video', file);

    // Cambiar UI al estado de carga
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('status-panel').style.display = 'block';

    try {
        const response = await fetch('http://localhost:5000/api/process-video', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        console.log('Server response:', result);
        
        if (result.success) {
            // Iniciar simulador de progreso (mientras el server procesa)
            simulateProgress();
        }
    } catch (error) {
        console.error('Error uploading video:', error);
        alert('Error al conectar con el motor de ViralFactory. ¿Está el server encendido?');
    }
}

function simulateProgress() {
    let progress = 0;
    const progressFill = document.querySelector('.progress-bar-fill');
    const percentageText = document.querySelector('.status-percentage');
    
    const interval = setInterval(() => {
        progress += Math.random() * 5;
        if (progress >= 100) {
            progress = 100;
            clearInterval(interval);
            alert('¡Video Editado y Programado para RRSS!');
        }
        progressFill.style.width = `${progress}%`;
        percentageText.innerText = `${Math.round(progress)}%`;
    }, 1000);
}
