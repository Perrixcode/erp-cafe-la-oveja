'use strict';
window.OvejaDateRange={
  validate(start,end){
    const valid=v=>/^\d{4}-\d{2}-\d{2}$/.test(v||'')&&v.slice(0,4)!=='0000'&&Number.isFinite(new Date(v+'T12:00:00Z').valueOf())&&new Date(v+'T12:00:00Z').toISOString().slice(0,10)===v;
    if(!valid(start)||!valid(end)||end<start)throw Error('Completa Desde y Hasta con fechas válidas; Hasta debe ser igual o posterior a Desde.');
    return {start,end};
  },
  today(){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
};
