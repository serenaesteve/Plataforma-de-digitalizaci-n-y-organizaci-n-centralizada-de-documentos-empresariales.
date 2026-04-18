# DocVault — Gestión Documental Empresarial

Plataforma de digitalización y organización centralizada de documentos empresariales.

## Instalación

```bash
pip install -r requirements.txt --break-system-packages
python app.py
```

Abre http://localhost:5000

## Credenciales por defecto
- **Usuario:** admin  
- **Contraseña:** admin123

## Funciones
- **Dashboard** con estadísticas en tiempo real
- **Subida de documentos** con drag & drop (PDF, DOCX, XLSX, PNG, JPG, TXT, CSV)
- **OCR automático** con Tesseract (extrae texto de PDFs escaneados e imágenes)
- **Análisis IA** con Ollama/LLaMA3: resumen ejecutivo, tipo de documento, datos clave, etiquetas
- **Carpetas** para organizar documentos
- **Búsqueda** por nombre, contenido y etiquetas
- **Gestión de usuarios** con roles (admin / editor / viewer)
- **Mover documentos** entre carpetas
- **Descarga** directa

## Requisitos para IA
Tener Ollama corriendo localmente con LLaMA3:
```bash
ollama run llama3
```

## Stack
- Flask + SQLite
- PyMuPDF (extracción PDF)
- pytesseract (OCR)
- Ollama local (IA)
