#include <stdint.h>
#include <string.h>

// Type definitions matching dataset_bindings.py
typedef void* rx_vm_t;

// Function pointers to the RandomX C library
typedef void (*rx_calc_first_t)(rx_vm_t vm, const void* input, size_t inputSize);
typedef void (*rx_calc_next_t)(rx_vm_t vm, const void* nextInput, size_t nextInputSize, void* output);

/**
 * Pipelined batched mining loop to avoid Python GIL overhead.
 * 
 * @param vm             RandomX VM instance
 * @param blob           Monero block hashing blob (mutable)
 * @param blob_size      Size of the blob (usually 76 bytes for rx/0)
 * @param nonce_offset   Offset of the 4-byte nonce (usually 39)
 * @param start_nonce    Starting nonce for this thread
 * @param batch_size     Number of hashes to compute in this batch
 * @param step           Nonce increment (usually num_workers)
 * @param target         64-bit target threshold (matches xmrig)
 * @param out_nonce      OUT: Nonce of a matching share (if any)
 * @param out_hash       OUT: 32-byte hash result of the matching share (if any)
 * @param hash_first_fn  Pointer to randomx_calculate_hash_first
 * @param hash_next_fn   Pointer to randomx_calculate_hash_next
 * 
 * @return Number of hashes actually computed. If < batch_size, a share was found.
 */
int rx_batch_mine(
    rx_vm_t vm,
    uint8_t* blob,
    size_t blob_size,
    int nonce_offset,
    uint32_t start_nonce,
    uint32_t batch_size,
    uint32_t step,
    uint64_t target,
    uint32_t* out_nonce,
    uint8_t* out_hash,
    rx_calc_first_t hash_first_fn,
    rx_calc_next_t hash_next_fn
) {
    if (batch_size == 0) return 0;

    uint32_t nonce = start_nonce;
    uint8_t hash_result[32];
    uint32_t* nonce_ptr = (uint32_t*)(blob + nonce_offset);

    // Prepare first hash (pipelined)
    *nonce_ptr = nonce;
    hash_first_fn(vm, blob, blob_size);
    nonce += step;

    for (uint32_t i = 1; i < batch_size; i++) {
        uint32_t prev_nonce = nonce - step;

        // Set next input for pipeline
        *nonce_ptr = nonce;

        // Calculate next hash + fetch previous result
        hash_next_fn(vm, blob, blob_size, hash_result);

        // Check if previous hash met the target
        // XMRig compares bytes [24:32] as a little-endian uint64
        uint64_t hash_val;
        memcpy(&hash_val, hash_result + 24, 8); // Safe unaligned read

        if (hash_val < target) {
            // Share found
            *out_nonce = prev_nonce;
            memcpy(out_hash, hash_result, 32);
            return i; // Stop early
        }

        nonce += step;
    }

    // Process last result
    uint32_t prev_nonce = nonce - step;
    *nonce_ptr = nonce; // Dummy next Input (ignored)
    hash_next_fn(vm, blob, blob_size, hash_result);

    uint64_t hash_val;
    memcpy(&hash_val, hash_result + 24, 8);

    if (hash_val < target) {
        *out_nonce = prev_nonce;
        memcpy(out_hash, hash_result, 32);
    }

    return batch_size;
}
