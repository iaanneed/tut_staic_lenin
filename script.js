const WIDE_BELARUS_BOUNDS = [16.884785,40.931153,38.198261,61.407894];
const BELARUS_BOUNDS = [23.17, 51.25, 32.77, 56.17];

const map = new maplibregl.Map({
    container: 'map', 
    style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    center: [28.0, 53.5], 
    zoom: 4.7,
    maxBounds: WIDE_BELARUS_BOUNDS
});

let geojsonDataCache = null;

const sidebar = document.getElementById('sidebar');
const sidebarContent = document.getElementById('sidebar-content');
const closeSidebarBtn = document.getElementById('close-sidebar-btn');
const modalOverlay = document.getElementById('about-modal-overlay');
const closeModalBtn = document.getElementById('close-modal-btn');
const lightboxOverlay = document.getElementById('lightbox-overlay');
const lightboxImage = document.getElementById('lightbox-image');
const tagInSidebar = document.getElementById('tag')
const tags = [
  '',           
  '#Гродзенская',
  '#Брэсцкая',
  '#Віцебская',
  '#Мінская',
  '#Гомельская',
  '#Магілеўская'
];

class FilterManager {
  constructor(map, layerId, filterProperty) {
    this.map = map;
    this.layerId = layerId;
    this.filterProperty = filterProperty;
    this.sourceData = null;
  }
  
  async loadSourceData() {
    if (this.sourceData) return this.sourceData;
    
    try {
      const response = await fetch('monuments.geojson');
      if (!response.ok) throw new Error('Failed to load GeoJSON');
      this.sourceData = await response.json();
      return this.sourceData;
    } catch (error) {
      console.error('Error loading source data:', error);
      return null;
    }
  }

  async applyFilter(tag) {
    if (!this.sourceData) {
      await this.loadSourceData();
    }

    const filter = tag ? ['==', ['get', this.filterProperty], tag] : null;
    this.map.setFilter(this.layerId, filter);

    const filteredFeatures = this.sourceData.features.filter(feature => {
      if (!tag) return true;
      return feature.properties[this.filterProperty] === tag;
    });

    if (filteredFeatures.length) {
      const bounds = new maplibregl.LngLatBounds();
      
      filteredFeatures.forEach(feature => {
        bounds.extend(feature.geometry.coordinates);
      });

      const isMobile = window.innerWidth <= 480;
      const padding = isMobile ? 20 : 100;

      this.map.fitBounds(bounds, {
        padding: padding,
        duration: 2000,
        maxZoom: 12
      });
    } else {
      console.warn('No features found for selected filter');
    }
  }
}

class AboutControl {
    onAdd(map) {
        this._map = map;
        this._container = document.createElement('div');
        this._container.className = 'maplibregl-ctrl maplibregl-ctrl-group';
        this._container.innerHTML = `<button type="button" aria-label="Аб праекце" title="Аб праекце"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="vertical-align: middle;"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 15c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1s1 .45 1 1v4c0 .55-.45 1-1 1zm1-8h-2V7h2v2z" fill="currentColor"/></svg></button>`;
        
        this._container.onclick = () => {
            modalOverlay.classList.remove('hidden');
        };
        return this._container;
    }

    onRemove() {
        this._container.parentNode.removeChild(this._container);
        this._map = undefined;
    }
}

class ResetViewControl {
    onAdd(map) {
        this._map = map;
        this._container = document.createElement('div');
        this._container.className = 'maplibregl-ctrl maplibregl-ctrl-group';
        this._container.innerHTML = '<button type="button" aria-label="Вярнуцца да пачатковага выгляду" title="Вярнуцца да пачатковага выгляду"><b>⛶</b></button>';
        
        this._container.onclick = () => {
            const options = {
                duration: 2300 
            };
            this._map.fitBounds(BELARUS_BOUNDS, options);
        };
        return this._container;
    }

    onRemove() {
        this._container.parentNode.removeChild(this._container);
        this._map = undefined;
    }
}

class LayerControl {
  constructor() {
    this.container = null;
    this.isPossibleVisible = false;
  }

  onAdd(map) {
    this.map = map;

    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl maplibregl-ctrl-group layer-control';

    this.container.innerHTML = `
      <button class="layer-toggle" title="Магчымыя Леніны">
        <svg width="18" height="18" viewBox="0 0 185 185" fill="currentColor" stroke="currentColor" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
          <path d="M151.083 161.875L102.521 113.313C98.6667 116.396 94.2344 118.837 89.224 120.635C84.2135 122.434 78.8819 123.333 73.2292 123.333C59.2257 123.333 47.3741 118.484 37.6745 108.784C27.9748 99.0842 23.125 87.2326 23.125 73.2292C23.125 59.2257 27.9748 47.3741 37.6745 37.6745C47.3741 27.9748 59.2257 23.125 73.2292 23.125C87.2326 23.125 99.0842 27.9748 108.784 37.6745C118.484 47.3741 123.333 59.2257 123.333 73.2292C123.333 78.8819 122.434 84.2135 120.635 89.224C118.837 94.2344 116.396 98.6667 113.313 102.521L161.875 151.083L151.083 161.875ZM73.2292 107.917C82.8646 107.917 91.0547 104.544 97.7995 97.7995C104.544 91.0547 107.917 82.8646 107.917 73.2292C107.917 63.5938 104.544 55.4036 97.7995 48.6589C91.0547 41.9141 82.8646 38.5417 73.2292 38.5417C63.5938 38.5417 55.4036 41.9141 48.6589 48.6589C41.9141 55.4036 38.5417 63.5938 38.5417 73.2292C38.5417 82.8646 41.9141 91.0547 48.6589 97.7995C55.4036 104.544 63.5938 107.917 73.2292 107.917Z"/>
        </svg>
      </button>
    `;

    this.toggleButton = this.container.querySelector('.layer-toggle');
    this.toggleButton.onclick = () => {
      this.togglePossibleLayer();
    };

    return this.container;
  }

  togglePossibleLayer() {
    this.isPossibleVisible = !this.isPossibleVisible;
    
    if (this.map.getLayer('possible-points')) {
      this.map.setLayoutProperty('possible-points', 'visibility', 
        this.isPossibleVisible ? 'visible' : 'none');
    }
    
    this.toggleButton.classList.toggle('active', this.isPossibleVisible);
  }

  onRemove() {
    this.container.parentNode.removeChild(this.container);
    this.map = undefined;
  }
}

class FilterControl {
  constructor(filterManager, tags) {
    this.filterManager = filterManager;
    this.tags = tags;
    this.container = null;
    this.isOpen = false;
    this.selectedTag = '';
  }

  onAdd(map) {
    this.map = map;

    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl maplibregl-ctrl-group filter';

    this.container.innerHTML = `
    <button class="filter-toggle">Вобласці ▼</button>
    <div class="filter-tags-container hidden">
        ${this.tags.map(tag => {
        const displayTag = tag.startsWith('#') ? tag.substring(1) : tag; 
        return `
            <button class="filter-tag" data-tag="${tag}">${displayTag || 'Усе'}</button>
        `;
        }).join('')}
    </div>
    `;

    this.toggleButton = this.container.querySelector('.filter-toggle');
    this.tagsContainer = this.container.querySelector('.filter-tags-container');

    this.toggleButton.onclick = () => this.toggleTags();

    this.tagsContainer.querySelectorAll('.filter-tag').forEach(btn => {
      btn.onclick = () => {
        const tag = btn.getAttribute('data-tag');
        this.selectedTag = tag;
        this.filterManager.applyFilter(tag);
        this.tagsContainer.querySelectorAll('.filter-tag').forEach(b => {
            b.classList.toggle('selected', b === btn); });        
        this.updateToggleButtonText(tag);
        this.closeTags();
      };
    });

    return this.container;
  }

  toggleTags() {
    this.isOpen = !this.isOpen;
    if (this.isOpen) {
      this.tagsContainer.classList.remove('hidden');
      this.container.classList.add('open');
    } else {
      this.closeTags();
    }
  }

  closeTags() {
    this.isOpen = false;
    this.tagsContainer.classList.add('hidden');
    this.container.classList.remove('open');
  }

  updateToggleButtonText(tag) {
    if (!tag || tag === '') {
      this.toggleButton.textContent = 'Вобласці ▼';
    } else {
      const displayTag = tag.startsWith('#') ? tag.substring(1) : tag;
      this.toggleButton.textContent = `${displayTag} ▼`;
    }
  }

  onRemove() {
    this.container.parentNode.removeChild(this.container);
    this.map = undefined;
  }
}

map.on('load', async () => { 
    try {
        map.addSource('monuments', {
            type: 'geojson',
            data: 'monuments.geojson'
        });

        map.addSource('possible-lenin', {
            type: 'geojson',
            data: 'possible_lenin.geojson'
        });

        const image = await map.loadImage('photos/image.png');
        const qpointerImage = await map.loadImage('photos/qpointer.png');
        
        map.addImage('monument-icon', image.data);
        map.addImage('qpointer-icon', qpointerImage.data);

        map.addLayer({
            id: 'monument-points',
            type: 'symbol',
            source: 'monuments',
            layout: {
                'icon-image': 'monument-icon',
                'icon-allow-overlap': true,
                'icon-anchor': 'bottom',
                'icon-size': [
                    'interpolate', 
                    ['linear'],    
                    ['zoom'],      
                    6, 0.05,     
                    15, 0.09,
                ]
            },
        });

        map.addLayer({
            id: 'possible-points',
            type: 'symbol',
            source: 'possible-lenin',
            layout: {
                'icon-image': 'qpointer-icon',
                'icon-allow-overlap': true,
                'icon-anchor': 'bottom',
                'icon-size': [
                    'interpolate', 
                    ['linear'],    
                    ['zoom'],      
                    6, 0.05,     
                    15, 0.09,
                ],
                'visibility': 'none'
            },
        });

        map.on('click', 'monument-points', (e) => {
            const feature = e.features[0];
            openSidebarForFeature(feature);
        });

        map.on('click', 'possible-points', (e) => {
            const feature = e.features[0];
            openSidebarForPossible(feature);
        });

        map.on('mouseenter', 'monument-points', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'monument-points', () => { map.getCanvas().style.cursor = ''; });
        map.on('mouseenter', 'possible-points', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'possible-points', () => { map.getCanvas().style.cursor = ''; });

        map.addControl(new AboutControl(), 'top-right');
        const filterManager = new FilterManager(map, 'monument-points', 'regionHashtag');
        const filterControl = new FilterControl(filterManager, tags);
        const layerControl = new LayerControl();
        map.addControl(new ResetViewControl(), 'top-right');
        map.addControl(filterControl, 'top-left');
        map.addControl(layerControl, 'top-left');
        map.addControl(
        new maplibregl.GeolocateControl({
            positionOptions: {
                enableHighAccuracy: true
            },
            trackUserLocation: true
        })
    );

        await handleUrlHash();

    } catch (error) {
        console.error('Не удалось загрузить ресурсы для карты:', error);
    }
});

closeSidebarBtn.addEventListener('click', () => {
    map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
    sidebar.classList.remove('visible');
    if (window.location.hash.startsWith('#monument/')) {
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }
});

function closeModal() {
    modalOverlay.classList.add('hidden');
}

closeModalBtn.addEventListener('click', closeModal);

modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) {
        closeModal();
    }
});

window.onpopstate = function(event) {
    if (!event.state || event.state.sidebar !== 'open') {
        map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
        sidebar.classList.remove('visible');
    }
};

sidebar.addEventListener('click', (e) => {
    if (e.target && e.target.classList.contains('card-image')) {
        lightboxImage.src = e.target.dataset.fullSrc;
        lightboxOverlay.classList.remove('hidden');
    }
    if (e.target && e.target.classList.contains('tag')) {
        const tag = e.target.textContent.trim();
        const filterControl = map._controls.find(ctrl => ctrl instanceof FilterControl);
        if (filterControl) {
            filterControl.selectedTag = tag.startsWith('#') ? tag : `#${tag}`;
            filterControl.filterManager.applyFilter(filterControl.selectedTag);
            filterControl.tagsContainer.querySelectorAll('.filter-tag').forEach(b => {
                b.classList.toggle('selected', b.getAttribute('data-tag') === filterControl.selectedTag);
            });
            filterControl.updateToggleButtonText(filterControl.selectedTag);
            filterControl.closeTags();
        }
    }
});

lightboxOverlay.addEventListener('click', () => {
    lightboxOverlay.classList.add('hidden');
});

tagInSidebar.addEventListener('click', () => {
    const tag = tagInSidebar.textContent.trim();
    const filterControl = map._controls.find(ctrl => ctrl instanceof FilterControl);
    if (filterControl) {
        filterControl.selectedTag = tag.startsWith('#') ? tag : `#${tag}`;
        filterControl.filterManager.applyFilter(filterControl.selectedTag);
        filterControl.tagsContainer.querySelectorAll('.filter-tag').forEach(b => {
            b.classList.toggle('selected', b.getAttribute('data-tag') === filterControl.selectedTag);
        });
        filterControl.updateToggleButtonText(filterControl.selectedTag);
        filterControl.closeTags();
    }
});


function openSidebarForFeature(feature) {
    const properties = feature.properties;
    const coordinates = feature.geometry.coordinates;
    const contentHTML = `
        <div class="card-container">
            <div class="card-image-wrapper">
                <img class="card-image" 
                    src="${properties.imageUrl_preview}" 
                    data-full-src="${properties.imageUrl_full}" 
                    alt="${properties.city || properties.title}">
            </div>
            <div class="card-text-wrapper">
                <header class="card-header">
                    <h2>${properties.city}</h2>
                </header>
                
                ${properties.title ? `<div class="card-body"><p>${properties.title}</p></div>` : ''}
    
                <div class="card-tags">
                    ${properties.regionHashtag ? `<span class="tag">${properties.regionHashtag}</span>` : ''}
                    ${properties.monumentType ? `<span class="tag">${properties.monumentType}</span>` : ''}
                </div>
            </div>
        </div>
    `;
    sidebarContent.innerHTML = contentHTML;

    const isMobile = window.innerWidth <= 480;
    let padding;
    if (isMobile) {
        sidebar.classList.add('visible', 'measure-helper');
        padding = { bottom: sidebar.offsetHeight };
        sidebar.classList.remove('visible', 'measure-helper');
    } else {
        padding = { right: sidebar.offsetWidth };
    }

    // Устанавливаем глобальный padding для карты
    // map.setPadding(padding);

    map.flyTo({
        center: coordinates,
        zoom: 15,
        speed: 1.5,
        padding: padding
    });

    history.pushState({sidebar: 'open', id: properties.source_id}, '', `#monument/${properties.source_id}`);

    sidebar.classList.add('visible');
}

function openSidebarForPossible(feature) {
    const properties = feature.properties;
    const coordinates = feature.geometry.coordinates;
    const name = properties.name || properties['name:be'] || properties['name:ru'] || 'Ленін';
    const lat = coordinates[1];
    const lng = coordinates[0];
    const coordString = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
    
    const isMobileDevice = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    const mapsLink = isMobileDevice 
        ? `geo:${lat},${lng}` 
        : `https://yandex.ru/maps/?pt=${lng},${lat}&z=15`;
    
    const contentHTML = `
        <div class="card-container">
            <div class="card-text-wrapper">
                <header class="card-header">
                    <h2>${name}</h2>
                </header>
                
                <div class="card-body">
                    <div class="coordinates-label-row">
                        <strong>Каардынаты:</strong>
                        <input type="text" class="coordinates-input" value="${coordString}" readonly>
                    </div>
                    <div class="coordinates-buttons">
                        <a href="${mapsLink}" class="coord-btn maps-btn" target="_blank">Мапы</a>
                        <a href="https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}&zoom=15" class="coord-btn osm-btn" target="_blank">OSM</a>
                        <button class="coord-btn copy-btn" data-coords="${coordString}" title="Копі">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
    sidebarContent.innerHTML = contentHTML;

    const copyBtn = sidebarContent.querySelector('.copy-btn');
    copyBtn.onclick = () => {
        const coords = copyBtn.getAttribute('data-coords');
        navigator.clipboard.writeText(coords).then(() => {
            const originalHTML = copyBtn.innerHTML;
            copyBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            setTimeout(() => {
                copyBtn.innerHTML = originalHTML;
            }, 2000);
        });
    };

    const isMobile = window.innerWidth <= 480;
    let padding;
    if (isMobile) {
        sidebar.classList.add('visible', 'measure-helper');
        padding = { bottom: sidebar.offsetHeight };
        sidebar.classList.remove('visible', 'measure-helper');
    } else {
        padding = { right: sidebar.offsetWidth };
    }

    map.flyTo({
        center: coordinates,
        zoom: 15,
        speed: 1.5,
        padding: padding
    });
    
    sidebar.classList.add('visible');
}

async function handleUrlHash() {
    if (window.location.hash.startsWith('#monument/')) {
        const objectId = parseInt(window.location.hash.replace('#monument/', ''), 10);
        if (isNaN(objectId)) return;

        try {
            let geojsonData;
            if (geojsonDataCache) {
                geojsonData = geojsonDataCache;
            } else {
                const response = await fetch('monuments.geojson');
                if (!response.ok) return;
                geojsonData = await response.json();
                
                geojsonDataCache = geojsonData;
            }
            const feature = geojsonData.features.find(f => f.properties.source_id === objectId);
            if (feature) {
                openSidebarForFeature(feature);
            }
        } catch (error) {
            console.error("Error processing URL hash:", error);
        }
    }
}