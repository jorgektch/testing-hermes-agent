'use client';

import {useCallback, useEffect, useMemo, useState} from 'react';
import {useParams} from 'next/navigation';
import {ArrowLeft, ChevronLeft, ChevronRight, Expand, Images, X} from 'lucide-react';
import styles from './gallery.module.css';

type Unit = {
  unit_id: string;
  commercial_name: string;
  property_type: string;
  media: {gallery_urls: string[]; gallery_url: string | null};
};
type PublicCatalog = {properties: Unit[]};

// The gallery is public and read-only. Accept only files that have already
// been published in the synthetic catalog, never arbitrary remote image URLs.
function approvedImage(path: string): boolean {
  return /^\/demo-assets\/[A-Za-z0-9_.-]+\.svg$/i.test(path);
}

export default function PropertyGallery() {
  const {unitId} = useParams<{unitId: string}>();
  const [unit, setUnit] = useState<Unit | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [index, setIndex] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoaded(false);
    setUnit(null);
    setIndex(0);
    setError('');
    fetch('/api/catalog', {cache:'no-store', signal:controller.signal})
      .then(async response => {
        if (!response.ok) throw new Error('Could not load the approved catalog.');
        return response.json() as Promise<PublicCatalog>;
      })
      .then(catalog => {
        const found = catalog.properties?.find(item => item.unit_id.toLowerCase() === unitId.toLowerCase());
        if (!found || !found.media?.gallery_url || found.media.gallery_url !== `/properties/${found.unit_id}/gallery`) {
          setError('There is no published gallery for this property.');
          return;
        }
        setUnit(found);
      })
      .catch(err => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Gallery unavailable.');
      })
      .finally(() => {if (!controller.signal.aborted) setLoaded(true);});
    return () => controller.abort();
  }, [unitId]);

  const images = useMemo(() => (unit?.media.gallery_urls ?? []).filter(approvedImage), [unit]);
  const move = useCallback((delta: number) => {
    setIndex(current => (current + delta + images.length) % images.length);
  }, [images.length]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setExpanded(false);
      if (images.length < 2) return;
      if (event.key === 'ArrowLeft') move(-1);
      if (event.key === 'ArrowRight') move(1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [images.length, move]);

  return <main className={styles.page}>
    <header className={styles.header}>
      <a className={styles.back} href="/#conversation"><ArrowLeft size={16}/> Back to Hermes Agent Lab</a>
      <div className={styles.brand}>black<span>penguin</span>.ai</div>
    </header>
    <section className={styles.content} aria-label="Property photo gallery">
      {!loaded ? <p className={styles.message}>Loading property gallery…</p> :
        error || !unit || images.length === 0 ? <div className={styles.empty}>
          <Images size={29}/><h1>Gallery unavailable</h1><p>{error || 'No approved images have been published for this property.'}</p>
          <a className={styles.back} href="/#conversation"><ArrowLeft size={15}/> Return to inventory</a>
        </div> : <>
          <div className={styles.heading}>
            <div><span className={styles.eyebrow}>APPROVED SYNTHETIC GALLERY</span><h1>{unit.commercial_name}</h1><p>{unit.unit_id} · {unit.property_type} · {images.length} {images.length === 1 ? 'image' : 'images'}</p></div>
            <span className={styles.counter}>{index + 1} / {images.length}</span>
          </div>
          <div className={styles.stage}>
            <button className={styles.expand} onClick={() => setExpanded(true)} aria-label="Expand photo"><Expand size={18}/></button>
            <img className={styles.mainImage} src={images[index]} alt={`Synthetic architectural illustration ${index+1} of ${unit.unit_id}`} />
            {images.length > 1 && <><button className={`${styles.arrow} ${styles.prev}`} onClick={() => move(-1)} aria-label="Previous image"><ChevronLeft size={23}/></button><button className={`${styles.arrow} ${styles.next}`} onClick={() => move(1)} aria-label="Next image"><ChevronRight size={23}/></button></>}
          </div>
          <div className={styles.thumbnails} aria-label="Choose gallery image">
            {images.map((src,i) => <button key={src} onClick={() => setIndex(i)} className={`${styles.thumb} ${i===index?styles.selected:''}`} aria-current={i===index?'true':undefined} aria-label={`View image ${i+1}`}><img src={src} alt={`Thumbnail ${i+1}`} /></button>)}
          </div>
          <p className={styles.disclaimer}>Illustrative assets for a fictional property. This gallery contains only images approved for this unit; no project-wide documents or lead information are shown.</p>
        </>}
    </section>
    {expanded && unit && images.length > 0 && <div className={styles.lightbox} role="dialog" aria-modal="true" aria-label={`Expanded image ${index+1} of ${unit.unit_id}`} onMouseDown={event => {if(event.target===event.currentTarget)setExpanded(false);}}>
      <button className={styles.close} aria-label="Close enlarged image" onClick={() => setExpanded(false)}><X size={22}/></button>
      <img src={images[index]} alt={`Enlarged synthetic architectural illustration ${index+1} of ${unit.unit_id}`}/>
      {images.length>1 && <div className={styles.lightboxControls}><button onClick={()=>move(-1)} aria-label="Previous enlarged image"><ChevronLeft size={20}/></button><span>{index+1} / {images.length}</span><button onClick={()=>move(1)} aria-label="Next enlarged image"><ChevronRight size={20}/></button></div>}
    </div>}
  </main>;
}
