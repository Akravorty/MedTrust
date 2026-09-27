import { useEffect, useState } from 'react';
import type { Facility } from '../types/schema';
import { listFacilities } from '../services/api';

export function useFacilities() {
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listFacilities()
      .then((data) => { if (!cancelled) setFacilities(data); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load facilities'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return { facilities, loading, error };
}

export function facilityLabel(f: Facility): string {
  return `${f.name} (${f.level.replace('_', ' ')})`;
}
