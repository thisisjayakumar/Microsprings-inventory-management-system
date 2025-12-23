# ========================================
# Base Image - Python 3.12 on Debian
# ========================================
FROM python:3.12-slim

# ========================================
# Environment Variables
# ========================================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ========================================
# Install System Dependencies
# ========================================
RUN apt-get update && apt-get install -y \
    # Build tools for Python packages
    gcc \
    pkg-config \
    # Image processing libraries (for Pillow)
    libjpeg-dev \
    zlib1g-dev \
    # Cleanup to reduce image size
    && rm -rf /var/lib/apt/lists/*

# ========================================
# Set Working Directory
# ========================================
WORKDIR /app

# ========================================
# Install Python Dependencies
# ========================================
# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python packages
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# ========================================
# Copy Application Code
# ========================================
COPY . .

# ========================================
# Create Necessary Directories
# ========================================
RUN mkdir -p /app/logs /app/media /app/staticfiles

# ========================================
# Collect Static Files
# ========================================
# This collects all Django admin and app static files
RUN python manage.py collectstatic --noinput --clear || true

# ========================================
# Expose Port
# ========================================
EXPOSE 8000

# ========================================
# Run Application
# ========================================
# Use Django development server for now (simpler for learning)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]