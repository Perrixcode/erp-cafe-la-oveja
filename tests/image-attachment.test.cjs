'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {error,LIMIT}=require('../static/image-attachment.js');
test('image paste accepts JPEG and PNG only within 8 MB',()=>{for(const type of ['image/jpeg','image/png'])assert.equal(error({type,size:LIMIT},true),'');for(const type of ['image/svg+xml','text/html','application/pdf','image/gif'])assert.ok(error({type,size:10},true));});
test('selector keeps PDF and rejects empty/oversize files',()=>{assert.equal(error({type:'application/pdf',size:100}),'');for(const size of [0,LIMIT+1])assert.ok(error({type:'image/png',size}));});
